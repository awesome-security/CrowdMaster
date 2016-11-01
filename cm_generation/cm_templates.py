# Copyright 2016 CrowdMaster Developer Team
#
# ##### BEGIN GPL LICENSE BLOCK ######
# This file is part of CrowdMaster.
#
# CrowdMaster is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# CrowdMaster is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CrowdMaster.  If not, see <http://www.gnu.org/licenses/>.
# ##### END GPL LICENSE BLOCK #####

import bpy
import mathutils
BVHTree = mathutils.bvhtree.BVHTree
KDTree = mathutils.kdtree.KDTree

from collections import OrderedDict

import random
import math
from math import radians

from ..libs.ins_vector import Vector
from ..libs.ins_octree import createOctreeFromBPYObjs

# ==================== Some base classes ====================


class Template():
    """Abstract super class.
    Templates are a description of how to create some arrangement of agents"""
    def __init__(self, inputs, settings, bpyName):
        """":param input: A list of Templates or GeoTemplates generated by the
        nodes that are connected to inputs of this node"""
        self.inputs = inputs
        self.bpyName = bpyName
        self.settings = settings

        self.buildCount = 0
        self.checkCache = None

    def build(self, pos, rot, scale, tags, cm_group):
        """Called when this template is being used to modify the scene"""
        self.buildCount += 1

    def check(self):
        """Return true if the inputs and gettings are correct"""
        return True


class GeoTemplate(Template):
    """Abstract super class.
    GeoTemplates are a description of how to create some arrangement of
     geometry"""
    def build(self, pos, rot, scale, group, deferGeo):
        """Called when this GeoTemplate is being used to modify the scene"""
        self.buildCount += 1

# ==================== End of base classes ====================


class GeoTemplateOBJECT(GeoTemplate):
    """For placing objects into the scene"""
    def build(self, pos, rot, scale, group, deferGeo):
        obj = bpy.context.scene.objects[self.settings["inputObject"]]
        if deferGeo:
            cp = bpy.data.objects.new("Empty", None)
            cp.matrix_world = obj.matrix_world
            cp["cm_deferObj"] = obj.name
        else:
            cp = obj.copy()
        group.objects.link(cp)
        bpy.context.scene.objects.link(cp)
        return cp

    def check(self):
        return self.settings["inputObject"] in bpy.context.scene.objects


class GeoTemplateGROUP(GeoTemplate):
    """For placing groups into the scene"""
    def build(self, pos, rot, scale, group, deferGeo):
        dat = bpy.data

        if deferGeo:
            for obj in dat.groups[self.settings["inputGroup"]].objects:
                if obj.type == 'ARMATURE':
                    newObj = obj.copy()
                    newObj.rotation_euler = rot
                    newObj.scale = Vector((scale, scale, scale))
                    newObj.location = pos
                    group.objects.link(newObj)
                    bpy.context.scene.objects.link(newObj)
                    newObj["cm_deferGroup"] = {"group": self.settings["inputGroup"],
                                               "aName": obj.name}
                    return newObj

        gp = [o for o in dat.groups[self.settings["inputGroup"]].objects]
        group_objects = [o.copy() for o in gp]

        topObj = None

        for obj in group_objects:
            if obj.parent in gp:
                obj.parent = group_objects[gp.index(obj.parent)]
            else:
                obj.rotation_euler = Vector(obj.rotation_euler) + rot
                obj.scale = Vector((scale, scale, scale))
                obj.location += pos

            group.objects.link(obj)
            bpy.context.scene.objects.link(obj)
            if obj.type == 'ARMATURE':
                aName = obj.name
                # TODO what if there is more than one armature?
            if obj.type == 'MESH':
                if len(obj.modifiers) > 0:
                    for mod in obj.modifiers:
                        if mod.type == "ARMATURE":
                            modName = mod.name
                            obj.modifiers[modName].object = dat.objects[aName]

            if obj.type == 'ARMATURE':
                topObj = obj

        if topObj is None:  # For if there is no armature object in the group
            zaxis = lambda x: x.location[2]
            bpy.ops.object.add(type='EMPTY',
                               location=min(group_objects, key=zaxis).location)
            e = bpy.context.object
            for obj in group_objects:
                if obj.parent not in group_objects:
                    obj.location -= pos
                    obj.parent = e
            topObj = e
        return topObj

    def check(self):
        return self.settings["inputGroup"] in bpy.data.groups


class GeoTemplateSWITCH(GeoTemplate):
    """Randomly (biased by "switchAmout") pick which of the inputs to use"""
    def build(self, pos, rot, scale, group, deferGeo):
        if random.random() < self.settings["switchAmout"]:
            return self.inputs["Object 1"].build(pos, rot, scale, group, deferGeo)
        else:
            return self.inputs["Object 2"].build(pos, rot, scale, group, deferGeo)

    def check(self):
        if "Object 1" not in self.inputs:
            return False
        if "Object 2" not in self.inputs:
            return False
        if not isinstance(self.inputs["Object 1"], GeoTemplate):
            return False
        if not isinstance(self.inputs["Object 2"], GeoTemplate):
            return False
        return True


class GeoTemplatePARENT(GeoTemplate):
    """Attach a piece of geo to a bone from the parent geo"""
    def build(self, pos, rot, scale, group, deferGeo):
        parent = self.inputs["Parent Group"].build(pos, rot, scale, group, deferGeo)
        child = self.inputs["Child Object"].build(pos, rot, scale, group, deferGeo)
        con = child.constraints.new("CHILD_OF")
        con.target = parent
        con.subtarget = self.settings["parentTo"]
        bone = parent.pose.bones[self.settings["parentTo"]]
        con.inverse_matrix = bone.matrix.inverted()
        if child.data:
            child.data.update()
        return parent
        # TODO check if the object has an armature modifier

    def check(self):
        if "Parent Group" not in self.inputs:
            return False
        if "Child Object" not in self.inputs:
            return False
        if not isinstance(self.inputs["Parent Group"], GeoTemplate):
            return False
        if not isinstance(self.inputs["Child Object"], GeoTemplate):
            return False
        # TODO check that object is in parent group
        return True


class TemplateADDTOGROUP(Template):
    """Change the group that agents are added to"""
    def build(self, pos, rot, scale, tags, cm_group):
        scene = bpy.context.scene
        isFrozen = False
        if scene.cm_groups.find(self.settings["groupName"]) != -1:
            group = scene.cm_groups[self.settings["groupName"]]
            isFrozen = group.freezePlacement
            if group.groupType == "auto":
                bpy.ops.scene.cm_groups_reset(groupName=self.settings["groupName"])
            else:
                return
        if isFrozen:
            return
        newGroup = scene.cm_groups.add()
        newGroup.name = self.settings["groupName"]
        group = scene.cm_groups[self.settings["groupName"]]
        self.inputs["Template"].build(pos, rot, scale, tags, group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        if self.settings["groupName"].strip() == "":
            return False
        return True


class TemplateAGENT(Template):
    """Create a CrowdMaster agent"""
    def build(self, pos, rot, scale, tags, cm_group, material="none", matSlotIndex=0):
        groupName = cm_group.name + "/" + self.settings["brainType"]
        new_group = bpy.data.groups.new(groupName)
        defG = self.settings["deferGeo"]
        topObj = self.inputs["Objects"].build(pos, rot, scale, new_group, defG)
        topObj.location = pos
        topObj.rotation_euler = rot
        topObj.scale = Vector((scale, scale, scale))
        
        if material != "none":
            topObj.material_slots[0].link = 'OBJECT'
            topObj.material_slots[0].material = bpy.data.materials[material]

        bpy.ops.scene.cm_agent_add(agentName=topObj.name,
                                   brainType=self.settings["brainType"],
                                   groupName=cm_group.name,
                                   geoGroupName=new_group.name)
        # TODO set tags

    def check(self):
        if "Objects" not in self.inputs:
            return False
        if not isinstance(self.inputs["Objects"], GeoTemplate):
            return False
        return True


class TemplateSWITCH(Template):
    """Randomly (biased by "switchAmout") pick which of the inputs to use"""
    def build(self, pos, rot, scale, tags, cm_group):
        if random.random() < self.settings["switchAmout"]:
            self.inputs["Template 1"].build(pos, rot, scale, tags, cm_group)
        else:
            self.inputs["Template 2"].build(pos, rot, scale, tags, cm_group)

    def check(self):
        if "Template 1" not in self.inputs:
            return False
        if "Template 2" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template 1"], Template):
            return False
        if isinstance(self.inputs["Template 1"], GeoTemplate):
            return False
        if not isinstance(self.inputs["Template 2"], Template):
            return False
        if isinstance(self.inputs["Template 2"], GeoTemplate):
            return False
        return True


class TemplateOFFSET(Template):
    """Modify the postion and/or the rotation of the request made"""
    def build(self, pos, rot, scale, tags, cm_group):
        nPos = Vector()
        nRot = Vector()
        if not self.settings["overwrite"]:
            nPos = Vector(pos)
            nRot = Vector(rot)
        if self.settings["referenceObject"] != "":
            refObj = bpy.data.objects[self.settings["referenceObject"]]
            nPos += refObj.location
            nRot += Vector(refObj.rotation_euler)
        nPos += self.settings["locationOffset"]
        tmpRot = self.settings["rotationOffset"]
        nRot += Vector((radians(tmpRot.x), radians(tmpRot.y), radians(tmpRot.z)))
        self.inputs["Template"].build(nPos, nRot, scale, tags, cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        ref = self.settings["referenceObject"]
        if ref != "" and ref not in bpy.context.scene.objects:
            return False
        return True


class TemplateRANDOM(Template):
    """Randomly modify rotation and scale of the request made"""
    def build(self, pos, rot, scale, tags, cm_group):
        rotDiff = random.uniform(self.settings["minRandRot"],
                                 self.settings["maxRandRot"])
        eul = mathutils.Euler(rot, 'XYZ')
        eul.rotate_axis('Z', math.radians(rotDiff))

        scaleDiff = random.uniform(self.settings["minRandSz"],
                                   self.settings["maxRandSz"])
        newScale = scale * scaleDiff
        
        allMats = []
        if self.settings["randMat"]:
            if self.settings["randMatPrefix"]:
                for mat in bpy.data.materials:
                    if self.settings["randMatPrefix"] in mat.name:
                        allMats.append(mat.name)
                        newMat = random.choice(allMats)
                        newSlotIndex = self.settings["slotIndex"]
                    else:
                        print("Prefix not found!")
                        newMat = "none"
                        newSlotIndex = 0
            else:
                print("You must enter a prefix!")
                newMat = "none"
                newSlotIndex = 0
        else:
            newMat = "none"
            newSlotIndex = 0

        self.inputs["Template"].build(pos, Vector(eul), newScale, tags, cm_group, material=newMat, matSlotIndex=newSlotIndex)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplatePOINTTOWARDS(Template):
    """Rotate to point towards object or closest point on mesh"""
    def __init__(self, inputs, settings, bpyName):
        Template.__init__(self, inputs, settings, bpyName)
        self.kdtree = None

    def build(self, pos, rot, scale, tags, cm_group):
        ob = bpy.context.scene.objects[self.settings["PointObject"]]
        if self.settings["PointType"] == "OBJECT":
            point = ob.location
        else:  # self.settings["PointObject"] == "MESH":
            if self.kdtree is None:
                mesh = ob.data
                self.kdtree = KDTree(len(mesh.vertices))
                for i, v in enumerate(mesh.vertices):
                    self.kdtree.insert(v.co, i)
                self.kdtree.balance()
            co, ind, dist = self.kdtree.find(ob.matrix_world.inverted() * pos)
            point = ob.matrix_world * co
        direc = point - pos
        rotQuat = direc.to_track_quat('Y', 'Z')
        self.inputs["Template"].build(pos, rotQuat.to_euler(), scale, tags, cm_group)

    def check(self):
        if self.settings["PointObject"] not in bpy.context.scene.objects:
            return False
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True



class TemplateCOMBINE(Template):
    """Duplicate request to all inputs"""
    def build(self, pos, rot, scale, tags, cm_group):
        for name, inp in self.inputs.items():
            print("name", name, inp.__class__.__name__)
            inp.build(pos, rot, scale, tags, cm_group)


class TemplateRANDOMPOSITIONING(Template):
    """Place randomly"""
    def build(self, pos, rot, scale, tags, cm_group):
        positions = []
        for a in range(self.settings["noToPlace"]):
            if self.settings["locationType"] == "radius":
                angle = random.uniform(-math.pi, math.pi)
                x = math.sin(angle)
                y = math.cos(angle)
                length = random.random() + random.random()
                if length > 1:
                    length = 2 - length
                length *= self.settings["radius"]
                x *= length
                y *= length
                diff = Vector((x, y, 0))
                diff.rotate(mathutils.Euler(rot))
                newPos = Vector(pos) + diff
                positions.append(newPos)
            elif self.settings["locationType"] == "sector":
                x = random.uniform(0, self.settings["MaxX"])
                y = random.uniform(0, self.settings["MaxY"])
                diff = Vector((x, y, 0))
                diff.rotate(mathutils.Euler(rot))
                newPos = Vector(pos) + diff
                positions.append(newPos)
        if self.settings["relax"]:
            radius = self.settings["relaxRadius"]
            for i in range(self.settings["relaxIterations"]):
                kd = KDTree(len(positions))
                for n, p in enumerate(positions):
                    kd.insert(p, n)
                kd.balance()
                for n, p in enumerate(positions):
                    adjust = Vector()
                    localPoints = kd.find_range(p, radius*2)
                    for (co, ind, dist) in localPoints:
                        if ind != n:
                            v = p - co
                            adjust += v * ((2*radius - v.length)/v.length)
                    if len(localPoints) > 0:
                        positions[n] += adjust/len(localPoints)
        for newPos in positions:
            self.inputs["Template"].build(newPos, rot, scale, tags, cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateFORMATION(Template):
    """Place in a row"""
    def build(self, pos, rot, scale, tags, cm_group):
        placePos = Vector(pos)
        diffRow = Vector((self.settings["ArrayRowMargin"], 0, 0))
        diffCol = Vector((0, self.settings["ArrayColumnMargin"], 0))
        diffRow.rotate(mathutils.Euler(rot))
        diffCol.rotate(mathutils.Euler(rot))
        diffRow *= scale
        diffCol *= scale
        number = self.settings["noToPlace"]
        rows = self.settings["ArrayRows"]
        for fullcols in range(number // rows):
            for row in range(rows):
                self.inputs["Template"].build(placePos + fullcols*diffCol +
                                              row*diffRow, rot, scale, tags, cm_group)
        for leftOver in range(number % rows):
            self.inputs["Template"].build(placePos + (number//rows)*diffCol + leftOver*diffRow, rot, scale, tags, cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateTARGET(Template):
    """Place based on the positions of vertices"""
    def build(self, pos, rot, scale, tags, cm_group):
        if self.settings["targetType"] == "object":
            objs = bpy.data.groups[self.settings["targetGroups"]].objects
            if self.settings["overwritePosition"]:
                for obj in objs:
                    self.inputs["Template"].build(obj.location,
                                                  Vector(obj.rotation_euler),
                                                  scale, tags, cm_group)
            else:
                for obj in objs:
                    loc = obj.location
                    oRot = Vector(obj.rotation_euler)
                    loc.rotate(mathutils.Euler(rot))
                    loc *= scale
                    self.inputs["Template"].build(loc + pos, rot + oRot,
                                                  scale, tags, cm_group)
        else:  # targetType == "vertex"
            obj = bpy.data.objects[self.settings["targetObject"]]
            if self.settings["overwritePosition"]:
                wrld = obj.matrix_world
                targets = [wrld*v.co for v in obj.data.vertices]
                newRot = Vector(obj.rotation_euler)
                for vert in targets:
                    self.inputs["Template"].build(vert, newRot, scale, tags,
                                                  cm_group)
            else:
                targets = [Vector(v.co) for v in obj.data.vertices]
                for loc in targets:
                    loc.rotate(mathutils.Euler(rot))
                    loc *= scale
                    self.inputs["Template"].build(loc + pos, rot, scale, tags,
                                                  cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        if self.settings["targetType"] == "object":
            if self.settings["targetGroups"] not in bpy.data.groups:
                return False
        elif self.settings["targetType"] == "vertex":
            if self.settings["targetObject"] not in bpy.context.scene.objects:
                return False
        return True


class TemplateOBSTACLE(Template):
    """Refuse any requests that are withing the bounding box of an obstacle"""
    def __init__(self, inputs, settings, bpyName):
        Template.__init__(self, inputs, settings, bpyName)
        self.octree = None

    def build(self, pos, rot, scale, tags, cm_group):
        if self.octree is None:
            objs = bpy.data.groups[self.settings["obstacleGroup"]].objects
            margin = self.settings["margin"]
            mVec = Vector((margin, margin, margin))
            radii = [(o.dimensions/2) + mVec for o in objs]
            self.octree = createOctreeFromBPYObjs(objs, allSpheres=False,
                                                  radii=radii)
        intersections = self.octree.checkPoint(pos)
        if len(intersections) == 0:
            self.inputs["Template"].build(pos, rot, scale, tags, cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        if self.settings["obstacleGroup"] not in bpy.data.groups:
            return False
        return True


class TemplateGROUND(Template):
    """Adjust the position of requests onto a ground mesh"""
    def __init__(self, inputs, settings, bpyName):
        Template.__init__(self, inputs, settings, bpyName)
        self.bvhtree = None

    def build(self, pos, rot, scale, tags, cm_group):
        sce = bpy.context.scene
        gnd = sce.objects[self.settings["groundMesh"]]
        if self.bvhtree is None:
            self.bvhtree = BVHTree.FromObject(gnd, sce)
        point = pos - gnd.location
        hitA, normA, indA, distA = self.bvhtree.ray_cast(point, (0, 0, -1))
        hitB, normB, indB, distB = self.bvhtree.ray_cast(point, (0, 0, 1))
        if hitA and hitB:
            if distA <= distB:
                hitA += gnd.location
                self.inputs["Template"].build(hitA, rot, scale, tags, cm_group)
            else:
                hitB += gnd.location
                self.inputs["Template"].build(hitB, rot, scale, tags, cm_group)
        elif hitA:
            hitA += gnd.location
            self.inputs["Template"].build(hitA, rot, scale, tags, cm_group)
        elif hitB:
            hitB += gnd.location
            self.inputs["Template"].build(hitB, rot, scale, tags, cm_group)

    def check(self):
        if self.settings["groundMesh"] not in bpy.context.scene.objects:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateSETTAG(Template):
    """Set a tag for an agent to start with"""
    def build(self, pos, rot, scale, tags, cm_group):
        tags[self.settings["tagName"]] = self.settings["tagValue"]
        self.inputs["Template"].build(pos, rot, scale, tags, cm_group)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False

templates = OrderedDict([
    ("ObjectInputNodeType", GeoTemplateOBJECT),
    ("GroupInputNodeType", GeoTemplateGROUP),
    ("GeoSwitchNodeType", GeoTemplateSWITCH),
    ("AddToGroupNodeType", TemplateADDTOGROUP),
    ("TemplateSwitchNodeType", TemplateSWITCH),
    ("ParentNodeType", GeoTemplatePARENT),
    ("TemplateNodeType", TemplateAGENT),
    ("OffsetNodeType", TemplateOFFSET),
    ("RandomNodeType", TemplateRANDOM),
    ("PointTowardsNodeType", TemplatePOINTTOWARDS),
    ("CombineNodeType", TemplateCOMBINE),
    ("RandomPositionNodeType", TemplateRANDOMPOSITIONING),
    ("FormationPositionNodeType", TemplateFORMATION),
    ("TargetPositionNodeType", TemplateTARGET),
    ("ObstacleNodeType", TemplateOBSTACLE),
    ("GroundNodeType", TemplateGROUND)
])
