# -*- coding: utf-8 -*-
# FreeCAD module initialisation (also runs in FreeCADCmd).
import os
import sys

import FreeCAD as App

try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = os.path.join(App.getUserAppDataDir(), "Mod", "PipeSolidBridge")
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

__version__ = "1.0.0"
