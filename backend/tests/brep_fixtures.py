"""Analytic test solids made directly with the CAD kernel (no solver imports)."""
import math
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeTorus
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.gp import gp_Pnt, gp_Vec, gp_Trsf, gp_Ax1, gp_Dir
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs


def transformed(shape, angle=0, translation=(0,0,0)):
    if angle:
        t=gp_Trsf();t.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(1,1,0)),math.radians(angle))
        shape=BRepBuilderAPI_Transform(shape,t,True).Shape()
    t=gp_Trsf();t.SetTranslation(gp_Vec(*translation))
    return BRepBuilderAPI_Transform(shape,t,True).Shape()


def cylinder(radius,length):return BRepPrimAPI_MakeCylinder(radius,length).Shape()


def tube(outer,inner,length,eccentricity=0):
    return BRepAlgoAPI_Cut(cylinder(outer,length),transformed(cylinder(inner,length),translation=(eccentricity,0,0))).Shape()


def polygon_tube(outer,inner,length):
    def prism(points):
        polygon=BRepBuilderAPI_MakePolygon()
        for x,y in points:polygon.Add(gp_Pnt(x,y,0))
        polygon.Close()
        return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(),gp_Vec(0,0,length)).Shape()
    return BRepAlgoAPI_Cut(prism(outer),prism(inner)).Shape()


def elbow(radius,outer,inner,angle):
    return BRepAlgoAPI_Cut(BRepPrimAPI_MakeTorus(radius,outer,math.radians(angle)).Shape(),
                          BRepPrimAPI_MakeTorus(radius,inner,math.radians(angle)).Shape()).Shape()


def write_step(shape,path):
    writer=STEPControl_Writer();writer.Transfer(shape,STEPControl_AsIs);writer.Write(str(path))
