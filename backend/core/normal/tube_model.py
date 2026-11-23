"""
Normal Shrink Tube - Tube Model
Reads DXF files to extract tube parameters.
"""
import os
import pandas as pd

try:
    import ezdxf
except ImportError:
    ezdxf = None


class TubeModelClass:
    """Parse tube DXF files and extract parameters."""

    def __init__(self, file_path):
        self.file_path = file_path
        self.params = None
        if file_path and os.path.exists(file_path):
            self._parse_dxf()

    def _parse_dxf(self):
        """Parse DXF file to extract tube parameters."""
        if ezdxf is None:
            raise ImportError("ezdxf is required for DXF parsing")

        doc = ezdxf.readfile(self.file_path)
        msp = doc.modelspace()

        params = {}
        # Extract text entities that contain parameter definitions
        for entity in msp.query('TEXT MTEXT'):
            text = entity.dxf.text if hasattr(entity.dxf, 'text') else entity.text
            if '=' in text:
                parts = text.split('=')
                if len(parts) == 2:
                    key = parts[0].strip()
                    try:
                        value = float(parts[1].strip())
                    except ValueError:
                        value = parts[1].strip()
                    params[key] = value

        # Build DataFrame with Parameter/Value columns
        self.params = pd.DataFrame({
            'Parameter': list(params.keys()),
            'Value': list(params.values())
        })

    def get_params(self):
        """Return parameters as DataFrame."""
        return self.params
