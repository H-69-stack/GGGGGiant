"""Scientific plots of real generated CAD meshes (not synthetic screenshots)."""
import json
from pathlib import Path
import numpy as np
import trimesh
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/mold_validation_20260921'
plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
plt.rcParams['axes.unicode_minus']=False


def main():
    figures=OUT/'figures';figures.mkdir(exist_ok=True)
    for file in sorted(OUT.glob('*_cavity.stl')):
        name=file.stem.removesuffix('_cavity')
        raw=json.loads((ROOT/'reports/stp_validation_20260921/measured'/f'{name}.json').read_text(encoding='utf8'))
        cl=np.array(raw['center_line_points'])
        cavity=trimesh.load(file,process=False)
        mold=trimesh.load(OUT/f'{name}_mold.stl',process=False)
        center=mold.bounds.mean(axis=0)
        cut=trimesh.intersections.slice_mesh_plane(mold,[0,-1,0],center,cap=False)
        fig=plt.figure(figsize=(15,4),facecolor='#f4f7fb')
        for i,(mesh,title,color) in enumerate([(cavity,'外径实心腔体 + 读取中心线','#769ab7'),(cut,'反切模坯 · Y 中面剖视','#377ba7')]):
            ax=fig.add_subplot(1,2,i+1,projection='3d',facecolor='#f4f7fb')
            verts=mesh.triangles
            coll=Poly3DCollection(verts,facecolor=color,edgecolor='none',alpha=.6 if i==0 else 1,rasterized=True)
            ax.add_collection3d(coll)
            ax.plot(*cl.T,color='#e94242',lw=2.8,label='测量中心线',zorder=10)
            low,high=mold.bounds
            ax.set_xlim(low[0],high[0]);ax.set_ylim(low[1],high[1]);ax.set_zlim(low[2],high[2])
            ax.set_box_aspect(high-low)
            if (high-low).argmax()==0:ax.view_init(elev=32,azim=75)
            else:ax.view_init(elev=15,azim=40)
            ax.set_axis_off()
            ax.set_title(title,fontsize=13,pad=0)
        fig.suptitle(f'{name}  |  有效截面 {len(raw["sections_full"])} 个',fontsize=16,color='#17364f')
        fig.text(.5,.015,'根据实际导出的 CAD 网格绘制；剖视仅用于查看内腔，STEP 为整体模坯。',ha='center',fontsize=10,color='#52647a')
        fig.savefig(figures/(name+'.png'),dpi=145,bbox_inches='tight');plt.close(fig)
        print(name,flush=True)


if __name__=='__main__':main()
