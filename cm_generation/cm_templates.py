# Copyright 2017 CrowdMaster Developer Team
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

import math
import random
from collections import OrderedDict
from math import radians
import os
import shutil

import bpy
import mathutils

from ..libs.ins_octree import createOctreeFromBPYObjs
from ..libs.ins_vector import Vector

BVHTree = mathutils.bvhtree.BVHTree
KDTree = mathutils.kdtree.KDTree




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

    def build(self, buildRequest):
        """Called when this template is being used to modify the scene"""
        self.buildCount += 1

    def check(self):
        """Return true if the inputs and settings are correct"""
        return True


class TemplateRequest():
    """Passed between the children of Template"""

    def __init__(self):
        self.pos = Vector((0, 0, 0))
        self.rot = Vector((0, 0, 0))
        self.scale = 1
        self.tags = {}
        self.cm_group = "cm_allAgents"

        self.materials = {}
        # Key: material to replace. Value: material to replace with

    def copy(self):
        new = TemplateRequest()
        new.pos = self.pos
        new.rot = self.rot
        new.scale = self.scale
        new.tags = self.tags.copy()
        new.cm_group = self.cm_group
        new.materials = self.materials.copy()
        return new

    def toGeoTemplate(self, deferGeo, group):
        new = GeoRequest()
        new.pos = self.pos
        new.rot = self.rot
        new.scale = self.scale
        new.tags = self.tags.copy()
        new.cm_group = self.cm_group
        new.group = group
        new.materials = self.materials.copy()
        new.deferGeo = deferGeo
        return new


class GeoTemplate(Template):
    """Abstract super class.
    GeoTemplates are a description of how to create some arrangement of
     geometry"""

    def build(self, pos, rot, scale, group, deferGeo):
        """Called when this GeoTemplate is being used to modify the scene"""
        self.buildCount += 1


class GeoRequest(TemplateRequest):
    """Passed between the children of GeoTemplate"""

    def __init__(self):
        TemplateRequest.__init__(self)
        self.deferGeo = False
        self.group = None

    def copy(self):
        new = GeoRequest()
        new.pos = self.pos
        new.rot = self.rot
        new.scale = self.scale
        new.tags = self.tags.copy()
        new.cm_group = self.cm_group
        new.group = self.group
        new.materials = self.materials.copy()
        new.deferGeo = self.deferGeo
        return new


class GeoReturn:
    """Object that is passed back by geo template nodes"""
    def __init__(self, obj):
        self.obj = obj
        self.overwriteRig = None
        self.constrainBone = None
        self.modifyBones = {}


# ==================== End of base classes ====================


class GeoTemplateOBJECT(GeoTemplate):
    """For placing objects into the scene"""

    def build(self, buildRequest):
        obj = bpy.context.scene.objects[self.settings["inputObject"]]
        if buildRequest.deferGeo:
            cp = bpy.data.objects.new("Empty", None)
            cp.matrix_world = obj.matrix_world
            cp["cm_deferObj"] = obj.name
            cp["cm_materials"] = buildRequest.materials
        else:
            cp = obj.copy()
            for m in cp.material_slots:
                if m.name in buildRequest.materials:
                    replacement = buildRequest.materials[m.name]
                    m.material = bpy.data.materials[replacement]
        buildRequest.group.objects.link(cp)
        bpy.context.scene.objects.link(cp)
        return GeoReturn(cp)

    def check(self):
        return self.settings["inputObject"] in bpy.context.scene.objects


class GeoTemplateGROUP(GeoTemplate):
    """For placing groups into the scene"""

    def build(self, buildRequest):
        dat = bpy.data

        pos = buildRequest.pos
        rot = buildRequest.rot
        scale = buildRequest.scale
        group = buildRequest.group
        deferGeo = buildRequest.deferGeo

        gp = [o for o in dat.groups[self.settings["inputGroup"]].objects]
        group_objects = [o.copy() for o in gp]

        def zaxis(x): return x.location[2]

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
                    newObj["cm_materials"] = buildRequest.materials
                    return GeoReturn(newObj)
            bpy.ops.object.add(type='EMPTY',
                               location=min(group_objects, key=zaxis).location)
            e = bpy.context.object
            group.objects.link(e)
            e["cm_deferGroup"] = {"group": self.settings["inputGroup"]}
            e["cm_materials"] = buildRequest.materials
            return GeoReturn(e)

        topObj = None

        for obj in group_objects:
            for m in obj.material_slots:
                if m.name in buildRequest.materials:
                    replacement = buildRequest.materials[m.name]
                    m.material = bpy.data.materials[replacement]

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
            bpy.ops.object.add(type='EMPTY',
                               location=min(group_objects, key=zaxis).location)
            e = bpy.context.object
            group.objects.link(e)
            for obj in group_objects:
                if obj.parent not in group_objects:
                    obj.location -= pos
                    obj.parent = e
            topObj = e
        return GeoReturn(topObj)

    def check(self):
        return self.settings["inputGroup"] in bpy.data.groups


def findUnusedGroup(searchDirectory, namePrefix, sourceGroup):
    for group in bpy.data.groups:
        if len(group.users_dupli_group) == 0 and group.name == sourceGroup:
            if group.library is not None:
                linkedFileName = os.path.split(group.library.filepath)[0]
                partialPath = os.path.join(searchDirectory, namePrefix)
                if linkedFileName[:len(partialPath)] == partialPath:
                    return group
    return False


def findUnusedFile(searchDirectory, namePrefix, sourceGroup):
    usedBlends = []
    for group in bpy.data.groups:
        if len(group.users_dupli_group) > 0 and group.name == sourceGroup:
            if group.library is not None:
                linkedFileName = os.path.split(group.library.filepath)[0]
                partialPath = os.path.join(searchDirectory, namePrefix)
                if linkedFileName[:len(partialPath)] == partialPath:
                    usedBlends.append(group.library.filepath)

    dupFiles = os.listdir(searchDirectory)

    for fileName in dupFiles:
        if fileName[:len(namePrefix)] == namePrefix:
            if fileName not in usedBlends:
                unusedFile = os.path.join(searchDirectory, fileName)
                with bpy.data.libraries.load(unusedFile, link=True) as (data_src, data_dst):
                    data_dst.groups = [group]
                return data_dst.groups[0]
    return False


def duplicateProxyLink(dupDir, sourceBlend, sourceGroup, sourceRig):
    if not os.path.exists(dupDir):
        os.makedirs(dupDir)

    newNamePrefix = "cm_" + os.path.split(sourceBlend)[1][:-6]

    dupliGroup = findUnusedGroup(dupDir, newNamePrefix, sourceGroup)

    if not dupliGroup:
        dupliGroup = findUnusedFile(dupDir, newNamePrefix, sourceGroup)

    if not dupliGroup:
        existingFiles = os.listdir(dupDir)
        count = 0
        while newNamePrefix + "_" + str(count) + ".blend" in existingFiles:
            count += 1
        newName = newNamePrefix + "_" + str(count) + ".blend"
        newfilepath = os.path.join(dupDir, newName)

        shutil.copyfile(sourceBlend, newfilepath)

        # append all groups from the .blend file
        with bpy.data.libraries.load(newfilepath, link=True) as (data_src, data_dst):
            data_dst.groups = [sourceGroup]

        dupliGroup = data_dst.groups[0]

    # add the group instance to the scene
    scene = bpy.context.scene
    ob = bpy.data.objects.new(newName, None)
    # data_dst.groups[0].name = "cm_" + newName + newName
    ob.dupli_group = dupliGroup
    ob.dupli_type = 'GROUP'
    scene.objects.link(ob)

    activeStore = bpy.context.scene.objects.active
    bpy.context.scene.objects.active = ob

    bpy.ops.object.proxy_make(object=sourceRig)
    rigObj = bpy.context.scene.objects.active

    bpy.context.scene.objects.active = activeStore

    return ob, rigObj


class GeoTemplateLINKGROUPNODE(GeoTemplate):
    def build(self, buildRequest):
        gret = self.inputs["Objects"].build(buildRequest)
        obj = gret.obj

        blendfile = os.path.split(bpy.data.filepath)[0]
        for d in self.settings["groupFile"][2:].split("/"):
            if d == "..":
                blendfile = os.path.split(blendfile)[0]
            else:
                blendfile = os.path.join(blendfile, d)

        dupDir = os.path.split(bpy.data.filepath)[0]
        for d in bpy.context.scene.cm_linked_file_dir[2:].split("/"):
            if d == "..":
                dupDir = os.path.split(dupDir)[0]
            else:
                dupDir = os.path.join(dupDir, d)

        group = self.settings["groupName"]
        rigObject = self.settings["rigObject"]

        newObj, newRig = duplicateProxyLink(dupDir, blendfile, group, rigObject)
        buildRequest.group.objects.link(newObj)
        buildRequest.group.objects.link(newRig)

        constrainBone = newRig.pose.bones[self.settings["constrainBone"]]

        lastActive = bpy.context.scene.objects.active
        bpy.context.scene.objects.active = newRig
        bpy.ops.object.posemode_toggle()
        armature = bpy.data.armatures[rigObject].bones
        armature.active = armature[self.settings["constrainBone"]]
        bpy.ops.pose.constraint_add(type="COPY_LOCATION")
        bpy.ops.pose.constraint_add(type="COPY_ROTATION")

        Cloc = newRig.pose.bones[self.settings["constrainBone"]].constraints[-2]
        Crot = newRig.pose.bones[self.settings["constrainBone"]].constraints[-1]

        Cloc.target = obj
        Cloc.use_z = False

        Crot.target = obj
        # Crot.use_offset = True

        bpy.ops.object.posemode_toggle()
        bpy.context.scene.objects.active = lastActive

        gret.overwriteRig = newRig
        gret.constrainBone = newRig.pose.bones[self.settings["constrainBone"]]

        return gret

    def check(self):
        if "Objects" not in self.inputs:
            return False
        if not isinstance(self.inputs["Objects"], GeoTemplate):
            return False
        return True


class GeoTemplateMODIFYBONE(GeoTemplate):
    def build(self, buildRequest):
        gret = self.inputs["Objects"].build(buildRequest)
        bn = self.settings["boneName"]
        if bn not in gret.modifyBones:
            gret.modifyBones[bn] = {}
        attrib = self.settings["attribute"]
        gret.modifyBones[bn][attrib] = self.settings["tagName"]
        return gret

    def check(self):
        if "Objects" not in self.inputs:
            return False
        if not isinstance(self.inputs["Objects"], GeoTemplate):
            return False
        return True


class GeoTemplateSWITCH(GeoTemplate):
    """Randomly (biased by "switchAmout") pick which of the inputs to use"""

    def build(self, buildRequest):
        if random.random() < self.settings["switchAmout"]:
            return self.inputs["Object 1"].build(buildRequest)
        else:
            return self.inputs["Object 2"].build(buildRequest)

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

    def build(self, buildRequest):
        gret = self.inputs["Parent Group"].build(buildRequest.copy())
        parent = gret.obj
        gret = self.inputs["Child Object"].build(buildRequest.copy())
        child = gret.obj
        con = child.constraints.new("CHILD_OF")
        con.target = parent
        con.subtarget = self.settings["parentTo"]
        bone = parent.pose.bones[self.settings["parentTo"]]
        con.inverse_matrix = bone.matrix.inverted()
        if child.data:
            child.data.update()
        return gret
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

    def build(self, buildRequest):
        scene = bpy.context.scene
        isFrozen = False
        if scene.cm_groups.find(self.settings["groupName"]) != -1:
            group = scene.cm_groups[self.settings["groupName"]]
            isFrozen = group.freezePlacement
            if group.groupType == "auto":
                bpy.ops.scene.cm_groups_reset(
                    groupName=self.settings["groupName"])
            else:
                return
        if isFrozen:
            return
        newGroup = scene.cm_groups.add()
        newGroup.name = self.settings["groupName"]
        buildRequest.cm_groups = self.settings["groupName"]
        self.inputs["Template"].build(buildRequest)

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


class TemplateRANDOMMATERIAL(Template):
    """Assign random materials"""

    def build(self, buildRequest):
        s = random.random() * self.settings["totalWeight"]
        index = 0
        mat = None
        while mat is None:
            s -= self.settings["materialList"][index][1]
            if s <= 0:
                mat = self.settings["materialList"][index][0]
            index += 1
        buildRequest.materials[self.settings["targetMaterial"]] = mat
        self.inputs["Template"].build(buildRequest)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateAGENT(Template):
    """Create a CrowdMaster agent"""

    def build(self, buildRequest):
        groupName = buildRequest.cm_group + "/" + self.settings["brainType"]
        newGp = bpy.data.groups.new(groupName)
        defG = self.settings["deferGeo"]
        pos = buildRequest.pos
        rot = buildRequest.rot
        scale = buildRequest.scale
        geoBuildRequest = buildRequest.toGeoTemplate(defG, newGp)
        gret = self.inputs["Objects"].build(geoBuildRequest)
        topObj = gret.obj

        topObj.location = pos
        topObj.rotation_euler = rot
        topObj.scale = Vector((scale, scale, scale))

        topObj["cm_randomMaterial"] = buildRequest.materials

        tags = buildRequest.tags
        packTags = [{"name": x, "value": tags[x]} for x in tags]

        rigOverwrite = gret.overwriteRig.name if gret.overwriteRig else ""
        constrainBone = gret.constrainBone.name if gret.constrainBone else ""

        packModifyBones = []
        for b in gret.modifyBones:
            for attribute in gret.modifyBones[b]:
                tag = gret.modifyBones[b][attribute]
                packModifyBones.append({"name": b,
                                        "attribute": attribute,
                                        "tag": tag})

        bpy.ops.scene.cm_agent_add(agentName=topObj.name,
                                   brainType=self.settings["brainType"],
                                   groupName=buildRequest.cm_group,
                                   geoGroupName=newGp.name,
                                   initialTags=packTags,
                                   rigOverwrite=rigOverwrite,
                                   constrainBone=constrainBone,
                                   modifyBones=packModifyBones)

    def check(self):
        if "Objects" not in self.inputs:
            return False
        if not isinstance(self.inputs["Objects"], GeoTemplate):
            return False
        return True


class TemplateSWITCH(Template):
    """Randomly (biased by "switchAmout") pick which of the inputs to use"""

    def build(self, buildRequest):
        if random.random() < self.settings["switchAmout"]:
            self.inputs["Template 1"].build(buildRequest)
        else:
            self.inputs["Template 2"].build(buildRequest)

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

    def build(self, buildRequest):
        nPos = Vector()
        nRot = Vector()
        if not self.settings["overwrite"]:
            nPos = Vector(buildRequest.pos)
            nRot = Vector(buildRequest.rot)
        if self.settings["referenceObject"] != "":
            refObj = bpy.data.objects[self.settings["referenceObject"]]
            nPos += refObj.location
            nRot += Vector(refObj.rotation_euler)
        nPos += self.settings["locationOffset"]
        tmpRot = self.settings["rotationOffset"]
        nRot += Vector((radians(tmpRot.x),
                        radians(tmpRot.y), radians(tmpRot.z)))
        buildRequest.pos = nPos
        buildRequest.rot = nRot
        self.inputs["Template"].build(buildRequest)

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

    def build(self, buildRequest):
        rotDiff = random.uniform(self.settings["minRandRot"],
                                 self.settings["maxRandRot"])
        eul = mathutils.Euler(buildRequest.rot, 'XYZ')
        eul.rotate_axis('Z', math.radians(rotDiff))

        scaleDiff = random.uniform(self.settings["minRandSz"],
                                   self.settings["maxRandSz"])
        newScale = buildRequest.scale * scaleDiff

        buildRequest.rot = Vector(eul)
        buildRequest.scale = newScale
        self.inputs["Template"].build(buildRequest)

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

    def build(self, buildRequest):
        ob = bpy.context.scene.objects[self.settings["PointObject"]]
        pos = buildRequest.pos
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
        buildRequest.rot = rotQuat.to_euler()
        self.inputs["Template"].build(buildRequest)

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

    def build(self, buildRequest):
        for name, inp in self.inputs.items():
            newBuildRequest = buildRequest.copy()
            inp.build(newBuildRequest)


class TemplateRANDOMPOSITIONING(Template):
    """Place randomly"""

    def build(self, buildRequest):
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
                diff.rotate(mathutils.Euler(buildRequest.rot))
                newPos = Vector(buildRequest.pos) + diff
                positions.append(newPos)
            elif self.settings["locationType"] == "area":
                MaxX = self.settings["MaxX"] / 2
                MaxY = self.settings["MaxY"] / 2
                x = random.uniform(-MaxX, MaxX)
                y = random.uniform(-MaxY, MaxY)
                diff = Vector((x, y, 0))
                newPos = Vector(buildRequest.pos) + diff
                positions.append(newPos)
            elif self.settings["locationType"] == "sector":
                direc = self.settings["direc"]
                angVar = self.settings["angle"] / 2
                angle = random.uniform(-angVar, angVar)
                x = math.sin(math.radians(angle + direc))
                y = math.cos(math.radians(angle + direc))
                length = random.random() + random.random()
                if length > 1:
                    length = 2 - length
                length *= self.settings["radius"]
                x *= length
                y *= length
                diff = Vector((x, y, 0))
                diff.rotate(mathutils.Euler(buildRequest.rot))
                newPos = Vector(buildRequest.pos) + diff
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
                    localPoints = kd.find_range(p, radius * 2)
                    for (co, ind, dist) in localPoints:
                        if ind != n:
                            v = p - co
                            adjust += v * ((2 * radius - v.length) / v.length)
                    if len(localPoints) > 0:
                        positions[n] += adjust / len(localPoints)
        for newPos in positions:
            newBuildRequest = buildRequest.copy()
            newBuildRequest.pos = newPos
            self.inputs["Template"].build(newBuildRequest)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateMESHPOSITIONING(Template):
    """Place randomly over the surface of a mesh"""

    def __init__(self, inputs, settings, bpyName):
        Template.__init__(self, inputs, settings, bpyName)
        self.bvhtree = None  # TODO use to relax points
        self.totalArea = None

    def build(self, buildRequest):
        guide = bpy.data.objects[self.settings["guideMesh"]]
        data = guide.data

        wrld = guide.matrix_world
        if self.totalArea is None:
            self.totalArea = sum(p.area for p in data.polygons)
        positions = []
        for n in range(self.settings["noToPlace"]):
            remaining = random.random() * self.totalArea
            index = 0
            while remaining > 0:
                remaining -= data.polygons[index].area
                if remaining <= 0:
                    a = data.vertices[data.polygons[index].vertices[0]].co
                    b = data.vertices[data.polygons[index].vertices[1]].co
                    c = data.vertices[data.polygons[index].vertices[2]].co
                    r1 = math.sqrt(random.random())
                    r2 = random.random()
                    pos = (1 - r1) * a + (r1 * (1 - r2)) * b + (r1 * r2) * c
                    if self.settings["overwritePosition"]:
                        pos = wrld * pos
                    else:
                        pos.rotate(mathutils.Euler(buildRequest.rot))
                        pos *= buildRequest.scale
                        pos = buildRequest.pos + pos
                    positions.append(pos)
                index += 1

        if self.settings["relax"]:
            sce = bpy.context.scene
            gnd = sce.objects[self.settings["guideMesh"]]
            if self.bvhtree is None:
                self.bvhtree = BVHTree.FromObject(gnd, sce)
            radius = self.settings["relaxRadius"]
            for i in range(self.settings["relaxIterations"]):
                kd = KDTree(len(positions))
                for n, p in enumerate(positions):
                    kd.insert(p, n)
                kd.balance()
                for n, p in enumerate(positions):
                    adjust = Vector()
                    localPoints = kd.find_range(p, radius * 2)
                    for (co, ind, dist) in localPoints:
                        if ind != n:
                            v = p - co
                            adjust += v * ((2 * radius - v.length) / v.length)
                    if len(localPoints) > 0:
                        adjPos = positions[n] + adjust / len(localPoints)
                        positions[n] = self.bvhtree.find_nearest(adjPos)[0]

        for newPos in positions:
            newBuildRequest = buildRequest.copy()
            newBuildRequest.pos = newPos
            self.inputs["Template"].build(newBuildRequest)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if self.settings["guideMesh"] not in bpy.context.scene.objects:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


class TemplateFORMATION(Template):
    """Place in a row"""

    def build(self, buildRequest):
        placePos = Vector(buildRequest.pos)
        diffRow = Vector((self.settings["ArrayRowMargin"], 0, 0))
        diffCol = Vector((0, self.settings["ArrayColumnMargin"], 0))
        diffRow.rotate(mathutils.Euler(buildRequest.rot))
        diffCol.rotate(mathutils.Euler(buildRequest.rot))
        diffRow *= buildRequest.scale
        diffCol *= buildRequest.scale
        number = self.settings["noToPlace"]
        rows = self.settings["ArrayRows"]
        for fullcols in range(number // rows):
            for row in range(rows):
                newBuildRequest = buildRequest.copy()
                newBuildRequest.pos = placePos + fullcols * diffCol + row * diffRow
                self.inputs["Template"].build(newBuildRequest)
        for leftOver in range(number % rows):
            newBuild = buildRequest.copy()
            newBuild.pos = placePos + \
                (number // rows) * diffCol + leftOver * diffRow
            self.inputs["Template"].build(newBuild)

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

    def build(self, buildRequest):
        if self.settings["targetType"] == "object":
            objs = bpy.data.groups[self.settings["targetGroups"]].objects
            if self.settings["overwritePosition"]:
                for obj in objs:
                    newBuildRequest = buildRequest.copy()
                    newBuildRequest.pos = obj.location
                    newBuildRequest.rot = Vector(obj.rotation_euler)
                    self.inputs["Template"].build(newBuildRequest)
            else:
                for obj in objs:
                    loc = obj.location
                    oRot = Vector(obj.rotation_euler)
                    loc.rotate(mathutils.Euler(buildRequest.rot))
                    loc *= buildRequest.scale
                    newBuildRequest = buildRequest.copy()
                    newBuildRequest.pos = loc + buildRequest.pos
                    newBuildRequest.rot = buildRequest.rot + oRot
                    self.inputs["Template"].build(newBuildRequest)
        else:  # targetType == "vertex"
            obj = bpy.data.objects[self.settings["targetObject"]]
            if self.settings["overwritePosition"]:
                wrld = obj.matrix_world
                targets = [wrld * v.co for v in obj.data.vertices]
                newRot = Vector(obj.rotation_euler)
                for vert in targets:
                    newBuildRequest = buildRequest.copy()
                    newBuildRequest.pos = vert
                    newBuildRequest.rot = newRot
                    self.inputs["Template"].build(newBuildRequest)
            else:
                targets = [Vector(v.co) for v in obj.data.vertices]
                for loc in targets:
                    loc.rotate(mathutils.Euler(buildRequest.rot))
                    loc *= buildRequest.scale
                    newBuildRequest = buildRequest.copy()
                    newBuildRequest.pos = loc + buildRequest.pos
                    self.inputs["Template"].build(newBuildRequest)

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

    def build(self, buildRequest):
        if self.octree is None:
            objs = bpy.data.groups[self.settings["obstacleGroup"]].objects
            margin = self.settings["margin"]
            mVec = Vector((margin, margin, margin))
            radii = [(o.dimensions / 2) + mVec for o in objs]
            self.octree = createOctreeFromBPYObjs(objs, allSpheres=False,
                                                  radii=radii)
        intersections = self.octree.checkPoint(buildRequest.pos)
        if len(intersections) == 0:
            self.inputs["Template"].build(buildRequest)

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

    def build(self, buildRequest):
        sce = bpy.context.scene
        gnd = sce.objects[self.settings["groundMesh"]]
        if self.bvhtree is None:
            self.bvhtree = BVHTree.FromObject(gnd, sce)
        point = buildRequest.pos - gnd.location
        hitA, normA, indA, distA = self.bvhtree.ray_cast(point, (0, 0, -1))
        hitB, normB, indB, distB = self.bvhtree.ray_cast(point, (0, 0, 1))
        if hitA and hitB:
            if distA <= distB:
                hitA += gnd.location
                buildRequest.pos = hitA
                self.inputs["Template"].build(buildRequest)
            else:
                hitB += gnd.location
                buildRequest.pos = hitB
                self.inputs["Template"].build(buildRequest)
        elif hitA:
            hitA += gnd.location
            buildRequest.pos = hitA
            self.inputs["Template"].build(buildRequest)
        elif hitB:
            hitB += gnd.location
            buildRequest.pos = hitB
            self.inputs["Template"].build(buildRequest)

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

    def build(self, buildRequest):
        buildRequest.tags[self.settings["tagName"]] = self.settings["tagValue"]
        self.inputs["Template"].build(buildRequest)

    def check(self):
        if "Template" not in self.inputs:
            return False
        if not isinstance(self.inputs["Template"], Template):
            return False
        if isinstance(self.inputs["Template"], GeoTemplate):
            return False
        return True


templates = OrderedDict([
    ("ObjectInputNodeType", GeoTemplateOBJECT),
    ("GroupInputNodeType", GeoTemplateGROUP),
    ("LinkGroupNodeType", GeoTemplateLINKGROUPNODE),
    ("ModifyBoneNodeType", GeoTemplateMODIFYBONE),
    ("GeoSwitchNodeType", GeoTemplateSWITCH),
    ("AddToGroupNodeType", TemplateADDTOGROUP),
    ("TemplateSwitchNodeType", TemplateSWITCH),
    ("ParentNodeType", GeoTemplatePARENT),
    ("RandomMaterialNodeType", TemplateRANDOMMATERIAL),
    ("TemplateNodeType", TemplateAGENT),
    ("OffsetNodeType", TemplateOFFSET),
    ("RandomNodeType", TemplateRANDOM),
    ("PointTowardsNodeType", TemplatePOINTTOWARDS),
    ("CombineNodeType", TemplateCOMBINE),
    ("RandomPositionNodeType", TemplateRANDOMPOSITIONING),
    ("MeshPositionNodeType", TemplateMESHPOSITIONING),
    ("FormationPositionNodeType", TemplateFORMATION),
    ("TargetPositionNodeType", TemplateTARGET),
    ("ObstacleNodeType", TemplateOBSTACLE),
    ("GroundNodeType", TemplateGROUND),
    ("SettagNodeType", TemplateSETTAG)
])
