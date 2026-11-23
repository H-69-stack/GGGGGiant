"""
STL Analysis Engine — Output format aligned with step_analyzer.analyze_shape().

Algorithm (mirrors reference STEP analyzer):
1. Load STL with trimesh
2. PCA to find long axis → rotation matrix to align with Z
3. Rotate mesh, slice at 1mm intervals along Z
4. For each slice: ConvexHull → outer_2d, inner_2d, outline(3D), perimeter, wall_thickness
5. Centroid chain → centerline → inverse-rotate to original coordinates
6. Output: sections (sparse, with 3D outline), sections_full (dense, with 2D data)
"""

import numpy as np
from scipy.spatial import ConvexHull

try:
    import trimesh
except ImportError:
    trimesh = None


def analyze_stl(file_path, n_sections=30, progress_callback=None):
    """
    Analyze an STL file. Output format matches step_analyzer.analyze_shape().

    Args:
        file_path: Path to .stl file
        n_sections: Number of sparse sections for 3D display
        progress_callback: callable(pct: int, message: str)

    Returns dict with:
        - sections: list[dict] sparse (every ~25mm), each has outline (3D closed loop)
        - sections_full: list[dict] dense (every 1mm), each has outer_2d, inner_2d
        - center_line_points: list of [x,y,z]
        - center_line_length: float
        - wall_thickness_stats: dict or None
        - bounding_box: [xmin, ymin, zmin, xmax, ymax, zmax]
    """
    if trimesh is None:
        return {'error': 'trimesh is not installed'}

    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    _progress(2, '加载STL模型...')

    # ── Step 1: Load mesh ──
    mesh = trimesh.load(file_path)
    if isinstance(mesh, trimesh.Scene):
        geoms = list(getattr(mesh, "geometry", {}).values())
        if geoms:
            mesh = trimesh.util.concatenate(geoms)
        else:
            return {'error': 'STL file contains no geometry'}
    if mesh.is_empty:
        return {'error': 'STL file is empty'}

    _progress(5, '计算主轴方向...')

    # ── Step 2: PCA to find long axis ──
    vertices = mesh.vertices
    centered = vertices - vertices.mean(axis=0)
    cov = np.cov(centered.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    long_axis = eigenvectors[:, -1]  # largest eigenvalue
    long_axis = long_axis / np.linalg.norm(long_axis)

    center = vertices.mean(axis=0)

    # Build rotation matrix: long_axis → Z
    z_target = np.array([0.0, 0.0, 1.0])
    rotation_matrix = _rotation_between_vectors(long_axis, z_target)

    _progress(8, '旋转模型对齐Z轴...')

    # ── Step 3: Rotate mesh ──
    rotated_vertices = (rotation_matrix @ (vertices - center).T).T
    rotated_mesh = trimesh.Trimesh(vertices=rotated_vertices, faces=mesh.faces)

    bounds = rotated_mesh.bounds
    z_min = bounds[0][2] + 1.0  # trim 1mm from each end
    z_max = bounds[1][2] - 1.0
    z_span = z_max - z_min

    if z_span <= 2.0:
        return {'error': f'模型Z轴范围太小: {z_span:.1f}mm'}

    # ── Step 4: Dense slicing at 1mm ──
    step_mm = 1.0
    z_values = np.arange(z_min, z_max, step_mm)
    total_cuts = len(z_values)

    _progress(10, f'开始切割 {total_cuts} 个截面...')

    raw_sections = []  # (centroid_3d_rotated, perimeter, wall_thickness, hull_2d, inner_2d, hull_3d_rotated)

    for idx, z in enumerate(z_values):
        if idx % 50 == 0:
            pct = 10 + int(70 * idx / max(total_cuts, 1))
            _progress(pct, f'切割截面 {idx}/{total_cuts}...')

        try:
            section = rotated_mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
            if section is None:
                continue

            # Get 2D path
            path_2d, transform_2d = section.to_planar()
            if path_2d is None or len(path_2d.vertices) < 3:
                continue

            pts_2d = path_2d.vertices  # Nx2 array

            # ConvexHull for outer envelope
            if len(pts_2d) < 3:
                continue
            hull = ConvexHull(pts_2d)
            hull_verts_2d = pts_2d[hull.vertices]

            # Perimeter = sum of hull edge lengths
            n_hull = len(hull_verts_2d)
            perimeter = 0.0
            for i in range(n_hull):
                d = hull_verts_2d[(i + 1) % n_hull] - hull_verts_2d[i]
                perimeter += float(np.linalg.norm(d))

            # X/Y axis lengths (for ellipse flattening)
            x_coords = hull_verts_2d[:, 0]
            y_coords = hull_verts_2d[:, 1]
            x_length = float(np.max(x_coords) - np.min(x_coords))
            y_length = float(np.max(y_coords) - np.min(y_coords))

            # Centroid of hull polygon (area-weighted)
            cx, cy, area_sum = 0.0, 0.0, 0.0
            for i in range(n_hull):
                x0, y0 = hull_verts_2d[i]
                x1, y1 = hull_verts_2d[(i + 1) % n_hull]
                cross = x0 * y1 - x1 * y0
                area_sum += cross
                cx += (x0 + x1) * cross
                cy += (y0 + y1) * cross
            if abs(area_sum) > 1e-12:
                cx /= (3.0 * area_sum)
                cy /= (3.0 * area_sum)
            else:
                cx = float(np.mean(hull_verts_2d[:, 0]))
                cy = float(np.mean(hull_verts_2d[:, 1]))

            centroid_rotated = np.array([cx, cy, z])

            # Inner hull (non-outer points)
            wall_thickness = None
            inner_hull_2d = None
            outer_set = set(hull.vertices.tolist())
            inner_pts = np.array([pts_2d[i] for i in range(len(pts_2d)) if i not in outer_set])

            if len(inner_pts) >= 3:
                try:
                    inner_hull = ConvexHull(inner_pts)
                    inner_hull_2d = inner_pts[inner_hull.vertices]

                    # Wall thickness by angular sampling
                    center_2d = np.array([cx, cy])
                    n_angles = 36
                    angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
                    thicknesses = []
                    for angle in angles:
                        direction = np.array([np.cos(angle), np.sin(angle)])
                        outer_r = float(np.max((hull_verts_2d - center_2d) @ direction))
                        inner_r = float(np.max((inner_hull_2d - center_2d) @ direction))
                        if outer_r > inner_r > 0:
                            thicknesses.append(outer_r - inner_r)
                    if thicknesses:
                        wall_thickness = float(np.median(thicknesses))
                except Exception:
                    pass

            # 3D hull points in rotated coords (for outline)
            hull_3d_rotated = np.column_stack([
                hull_verts_2d[:, 0],
                hull_verts_2d[:, 1],
                np.full(n_hull, z)
            ])

            raw_sections.append({
                'centroid_rotated': centroid_rotated,
                'perimeter': perimeter,
                'wall_thickness': wall_thickness,
                'hull_2d': hull_verts_2d.tolist(),
                'inner_2d': inner_hull_2d.tolist() if inner_hull_2d is not None else None,
                'hull_3d_rotated': hull_3d_rotated,
                'x_length': x_length,
                'y_length': y_length,
            })

        except Exception:
            continue

    if len(raw_sections) < 4:
        return {'error': f'有效截面太少({len(raw_sections)}个)，请检查模型'}

    _progress(82, '过滤异常截面...')

    # ── Step 5: Filter outlier sections ──
    perimeters = [s['perimeter'] for s in raw_sections]
    median_p = float(np.median(perimeters))
    if median_p > 0:
        raw_sections = [s for s in raw_sections if s['perimeter'] <= median_p * 1.4]

    if len(raw_sections) < 4:
        return {'error': '过滤后有效截面太少'}

    _progress(85, '反变换到原始坐标...')

    # ── Step 6: Inverse-rotate to original coordinates ──
    inv_rotation = rotation_matrix.T  # orthogonal matrix, inverse = transpose

    for sec in raw_sections:
        # Centroid → original coords
        sec['centroid_original'] = (inv_rotation @ sec['centroid_rotated']) + center
        # Hull 3D → original coords
        hull_3d_orig = (inv_rotation @ sec['hull_3d_rotated'].T).T + center
        # Close the loop
        outline = hull_3d_orig.tolist()
        outline.append(outline[0])
        sec['outline'] = outline

    _progress(88, '构建输出数据...')

    # ── Step 7: Build output ──
    # Cumulative lengths
    positions = [s['centroid_original'] for s in raw_sections]
    cum_lengths = _compute_cumulative_lengths(positions)

    # sections_full (dense, every 1mm)
    sections_full = []
    for i, sec in enumerate(raw_sections):
        entry = {
            'position': [float(x) for x in sec['centroid_original']],
            'perimeter': round(float(sec['perimeter']), 2),
            'cumulative_length': round(cum_lengths[i], 2),
        }
        if sec['wall_thickness'] is not None:
            entry['wall_thickness'] = round(float(sec['wall_thickness']), 2)
        entry['outer_2d'] = [[round(p[0], 3), round(p[1], 3)] for p in sec['hull_2d']]
        if sec['inner_2d'] is not None:
            entry['inner_2d'] = [[round(p[0], 3), round(p[1], 3)] for p in sec['inner_2d']]
        else:
            entry['inner_2d'] = None
        if 'x_length' in sec:
            entry['x_length'] = round(float(sec['x_length']), 2)
            entry['y_length'] = round(float(sec['y_length']), 2)
        sections_full.append(entry)

    # sections (sparse, for 3D display — every ~25mm)
    total_length = cum_lengths[-1] if cum_lengths else 0
    if len(raw_sections) <= n_sections:
        sparse_indices = list(range(len(raw_sections)))
    else:
        sparse_indices = np.round(np.linspace(0, len(raw_sections) - 1, n_sections)).astype(int).tolist()

    sections = []
    for i in sparse_indices:
        sec = raw_sections[i]
        entry = {
            'position': [float(x) for x in sec['centroid_original']],
            'perimeter': round(float(sec['perimeter']), 2),
            'cumulative_length': round(cum_lengths[i], 2),
            'outline': [[round(p[0], 3), round(p[1], 3), round(p[2], 3)] for p in sec['outline']],
        }
        if sec['wall_thickness'] is not None:
            entry['wall_thickness'] = round(float(sec['wall_thickness']), 2)
        sections.append(entry)

    # Center line points (from sparse sections)
    center_line_points = [s['position'] for s in sections]

    # Wall thickness statistics
    wt_values = [s['wall_thickness'] for s in sections_full if s.get('wall_thickness') is not None]
    wt_stats = None
    if wt_values:
        wt_arr = np.array(wt_values)
        wt_stats = {
            'median_mm': round(float(np.median(wt_arr)), 2),
            'mean_mm': round(float(np.mean(wt_arr)), 2),
            'min_mm': round(float(np.min(wt_arr)), 2),
            'max_mm': round(float(np.max(wt_arr)), 2),
            'count': len(wt_values),
        }

    # Bounding box (original coords)
    bbox = mesh.bounds  # [[xmin,ymin,zmin],[xmax,ymax,zmax]]
    bounding_box = [
        float(bbox[0][0]), float(bbox[0][1]), float(bbox[0][2]),
        float(bbox[1][0]), float(bbox[1][1]), float(bbox[1][2]),
    ]

    _progress(95, '分析完成')

    return {
        'sections': sections,
        'sections_full': sections_full,
        'center_line_points': center_line_points,
        'center_line_length': round(total_length, 2),
        'wall_thickness_stats': wt_stats,
        'bounding_box': bounding_box,
    }


def _rotation_between_vectors(vec_from, vec_to):
    """Compute rotation matrix that rotates vec_from to vec_to."""
    a = vec_from / np.linalg.norm(vec_from)
    b = vec_to / np.linalg.norm(vec_to)
    v = np.cross(a, b)
    c = np.dot(a, b)

    if np.linalg.norm(v) < 1e-10:
        if c > 0:
            return np.eye(3)
        else:
            # 180 degree rotation — find perpendicular axis
            perp = np.array([1, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1, 0])
            perp = perp - np.dot(perp, a) * a
            perp = perp / np.linalg.norm(perp)
            # Rotation 180 degrees around perp
            return 2 * np.outer(perp, perp) - np.eye(3)

    vx = np.array([
        [0, -v[2], v[1]],
        [v[2], 0, -v[0]],
        [-v[1], v[0], 0]
    ])
    R = np.eye(3) + vx + vx @ vx / (1 + c)
    return R


def _compute_cumulative_lengths(positions):
    """Compute cumulative arc length along a polyline of 3D points."""
    if not positions:
        return []
    cum = [0.0]
    for i in range(1, len(positions)):
        p1 = np.array(positions[i - 1])
        p2 = np.array(positions[i])
        cum.append(cum[-1] + float(np.linalg.norm(p2 - p1)))
    return cum
