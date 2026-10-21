"""Standard-library glTF retarget preparation for a later Blender FBX export.

This reproduces the inspected viewer's model-space rotation transfer, rest-height
hip scaling, in-place hip anchoring, fixed fingers and optional ground lift.
It does not reproduce the viewer's stateful foot locking or contextual blends.
No asset-dependent result has been verified in the cloud recovery environment.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import re
import statistics
import struct
from pathlib import Path


def qmul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    if (not isinstance(q, (tuple,list)) or len(q) != 4
            or any(isinstance(v,bool) or not isinstance(v,(int,float))
                   or not math.isfinite(v) for v in q)):
        raise ValueError("Quaternion must contain four finite numbers")
    n = math.hypot(*q)
    if n < 1e-12:
        raise ValueError("Zero-length quaternion")
    return tuple(v/n for v in q)


def qslerp(a, b, t):
    # Canonical Int16 components do not decode to perfectly unit quaternions.
    # Normalize before interpolation/matrix composition, not only after the
    # world-space retarget, where a nonunit input has already introduced skew.
    a,b = qnorm(a),qnorm(b)
    if not math.isfinite(t):
        raise ValueError("Quaternion interpolation time must be finite")
    if t <= 0:
        return a
    if t >= 1:
        return b
    d = sum(x*y for x, y in zip(a, b))
    if d < 0:
        b, d = tuple(-x for x in b), -d
    if d > 0.9995:
        return qnorm(tuple(x+(y-x)*t for x, y in zip(a, b)))
    theta = math.acos(max(-1.0, min(1.0, d)))
    st = math.sin(theta)
    wa, wb = math.sin((1-t)*theta)/st, math.sin(t*theta)/st
    return qnorm(tuple(x*wa+y*wb for x, y in zip(a, b)))


IDENTITY = (1.,0.,0.,0., 0.,1.,0.,0., 0.,0.,1.,0., 0.,0.,0.,1.)


def mmul(a, b):
    """Column-major affine/general 4x4 product, matching glTF and Three.js."""
    return tuple(sum(a[k*4+r]*b[c*4+k] for k in range(4))
                 for c in range(4) for r in range(4))


def compose(p=(0,0,0), q=(0,0,0,1), s=(1,1,1)):
    x,y,z,w = q
    xx,yy,zz,xy,xz,yz,wx,wy,wz = x*x,y*y,z*z,x*y,x*z,y*z,w*x,w*y,w*z
    sx,sy,sz = s
    return ((1-2*(yy+zz))*sx, 2*(xy+wz)*sx, 2*(xz-wy)*sx, 0.,
            2*(xy-wz)*sy, (1-2*(xx+zz))*sy, 2*(yz+wx)*sy, 0.,
            2*(xz+wy)*sz, 2*(yz-wx)*sz, (1-2*(xx+yy))*sz, 0.,
            float(p[0]), float(p[1]), float(p[2]), 1.)


def point(m, p):
    return tuple(m[r]*p[0]+m[4+r]*p[1]+m[8+r]*p[2]+m[12+r] for r in range(3))


def inverse_affine(m):
    a,b,c,d,e,f,g,h,i = m[0],m[4],m[8],m[1],m[5],m[9],m[2],m[6],m[10]
    det = a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
    if abs(det) < 1e-14:
        raise ValueError("Singular node transform")
    A,B,C = (e*i-f*h)/det,(c*h-b*i)/det,(b*f-c*e)/det
    D,E,F = (f*g-d*i)/det,(a*i-c*g)/det,(c*d-a*f)/det
    G,H,I = (d*h-e*g)/det,(b*g-a*h)/det,(a*e-b*d)/det
    tx,ty,tz = m[12:15]
    return (A,D,G,0., B,E,H,0., C,F,I,0.,
            -(A*tx+B*ty+C*tz),-(D*tx+E*ty+F*tz),-(G*tx+H*ty+I*tz),1.)


def decompose(m):
    """Three.js-style matrix decomposition. Reject reflected input rig scales."""
    s = tuple(math.sqrt(sum(m[c*4+r]**2 for r in range(3))) for c in range(3))
    if min(s) < 1e-12:
        raise ValueError("Zero node scale")
    a,b,c,d,e,f,g,h,i = (m[0]/s[0],m[4]/s[1],m[8]/s[2],
                        m[1]/s[0],m[5]/s[1],m[9]/s[2],
                        m[2]/s[0],m[6]/s[1],m[10]/s[2])
    if a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g) <= 0:
        raise ValueError("Reflected rig transform needs an explicit conversion")
    tr = a+e+i
    if tr > 0:
        v = .5/math.sqrt(tr+1)
        q = ((h-f)*v,(c-g)*v,(d-b)*v,.25/v)
    elif a > e and a > i:
        v = 2*math.sqrt(max(0.,1+a-e-i))
        q = (.25*v,(b+d)/v,(c+g)/v,(h-f)/v)
    elif e > i:
        v = 2*math.sqrt(max(0.,1+e-a-i))
        q = ((b+d)/v,.25*v,(f+h)/v,(c-g)/v)
    else:
        v = 2*math.sqrt(max(0.,1+i-a-e))
        q = ((c+g)/v,(f+h)/v,.25*v,(d-b)/v)
    return tuple(m[12:15]), q, s


def clean_name(name):
    return name[len("mixamorig:"):] if name.startswith("mixamorig:") else (
        name[len("mixamorig"):] if name.startswith("mixamorig") else name)


def prepare_fixed_fingers(source, target, library):
    """Ignore only proven unused terminal placeholders in the legacy index.

    Real uploaded data contains zero Hand*4 rotations for source-only leaf
    joints. They affect no target joint or ancestor and have no animated track.
    Zero rotations on any effective joint still fail normal validation.
    """
    prepared, ignored = {}, []
    animated = {clean_name(track[0]) for clip in library.doc.get("clips", {}).values()
                for track in clip.get("tracks", [])}
    fingers = library.doc.get("fingers", {})
    if not isinstance(fingers, dict):
        raise ValueError("Fixed finger pose must be a named object")
    for name, rotation in fingers.items():
        if name != clean_name(name) or name not in source.bones:
            raise ValueError(f"Unknown fixed-pose bone: {name}")
        zero = (isinstance(rotation, (tuple, list)) and len(rotation) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v) and v == 0 for v in rotation))
        if (zero and re.fullmatch(r"(?:Left|Right)Hand(?:Thumb|Index|Middle|Ring|Pinky)4", name)
                and name not in target.bones and name not in animated
                and not source.nodes[source.bones[name]].get("children")):
            ignored.append(name)
            continue
        prepared[name] = qnorm(rotation)
    return prepared, sorted(ignored)


def clip_sample_window(library, clip_id):
    """Retain canonical endpoint clamping, bounded to one source sample interval."""
    clip = library.doc["clips"][clip_id]
    count, start = clip["frames"], clip["time"]
    if (isinstance(count, bool) or not isinstance(count, int) or count < 2
            or isinstance(start, bool) or not isinstance(start, int) or start < 0):
        raise ValueError(f"Invalid clip time range: {clip_id}")
    times = library.times[start:start + count]
    if (len(times) != count or not all(math.isfinite(t) for t in times)
            or any(b <= a for a, b in zip(times, times[1:]))):
        raise ValueError(f"Invalid clip time samples: {clip_id}")
    duration = library.doc["meta"][clip_id]["duration"]
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration <= 0):
        raise ValueError(f"Invalid clip metadata duration: {clip_id}")
    interval = statistics.median(b - a for a, b in zip(times, times[1:]))
    leading, trailing = max(0., times[0]), max(0., duration - times[-1])
    tolerance = 1e-5
    if (times[0] < -tolerance or times[-1] > duration + tolerance
            or max(leading, trailing) > interval + tolerance):
        raise ValueError(f"Clip endpoint hold exceeds one source sample interval: {clip_id}")
    return {"first_sample_seconds": times[0], "last_sample_seconds": times[-1],
            "metadata_duration_seconds": duration, "median_sample_interval_seconds": interval,
            "leading_hold_seconds": leading, "trailing_hold_seconds": trailing,
            "policy": "Clamp outside recorded samples without shifting or stretching source time"}


def pass_opening_hold_record(library):
    """Describe an opt-in derived hold, never a rewrite of canonical samples."""
    clip = library.doc["clips"]["pass"]
    count = clip["frames"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 3:
        raise ValueError("Pass opening hold requires at least three stored samples")
    window = clip_sample_window(library, "pass")
    first, second = library.times[clip["time"]:clip["time"] + 2]
    if first != 0.0 or not 0.0 < second <= 0.05:
        raise ValueError("Pass opening hold requires zero start and a second sample within 0.05 seconds")
    phase = library.doc["meta"]["pass"].get("contact")
    if (isinstance(phase, bool) or not isinstance(phase, (int, float))
            or not math.isfinite(phase) or not 0 < phase < 1):
        raise ValueError("Pass opening hold requires a valid unchanged contact phase")
    contact = phase * window["metadata_duration_seconds"]
    if contact <= second:
        raise ValueError("Pass opening hold must end before the contact sample")
    return {"kind": "hold_pass_opening_sample", "animation_id": "pass",
            "source_sample_index": 1, "source_sample_time_seconds": second,
            "affected_start_seconds": 0.0, "affected_end_seconds_exclusive": second,
            "source_duration_seconds_unchanged": window["metadata_duration_seconds"],
            "contact_seconds_unchanged": contact, "source_arrays_modified": False,
            "later_source_timestamps_shifted": False,
            "scope": "Derived continuity correction shared by skeletal pose and actor root travel",
            "provenance": "Opt-in hold of stored sample one, not recovered original capture"}


def read_glb(path):
    raw = Path(path).read_bytes()
    if len(raw) < 20 or struct.unpack_from("<III", raw) != (0x46546c67, 2, len(raw)):
        raise ValueError(f"Invalid glTF 2 GLB: {path}")
    offset, document, binary = 12, None, None
    while offset < len(raw):
        size, kind = struct.unpack_from("<II", raw, offset)
        chunk = raw[offset+8:offset+8+size]
        if len(chunk) != size:
            raise ValueError("Truncated GLB chunk")
        if kind == 0x4e4f534a:
            document = json.loads(chunk)
        elif kind == 0x004e4942:
            binary = chunk
        offset += 8+size
    if document is None or binary is None or len(document.get("buffers", [])) != 1:
        raise ValueError("Expected one embedded GLB buffer")
    if document["buffers"][0].get("uri"):
        raise ValueError("External GLB buffers are not supported")
    return document, binary


def write_glb(path, doc, binary):
    doc = copy.deepcopy(doc)
    doc["buffers"] = [{"byteLength": len(binary)}]
    j = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode()
    j += b" "*((-len(j)) % 4)
    b = bytes(binary)+b"\0"*((-len(binary)) % 4)
    raw = (struct.pack("<III", 0x46546c67, 2, 28+len(j)+len(b))
           +struct.pack("<II", len(j), 0x4e4f534a)+j
           +struct.pack("<II", len(b), 0x004e4942)+b)
    Path(path).write_bytes(raw)


class Rig:
    def __init__(self, doc):
        self.doc = doc
        self.nodes = doc["nodes"]
        self.parent = {}
        for idx, node in enumerate(self.nodes):
            for child in node.get("children", []):
                if child in self.parent:
                    raise ValueError("Node has multiple parents")
                self.parent[child] = idx
        self.joints = set(j for skin in doc.get("skins", []) for j in skin["joints"])
        self.bones = {}
        for idx in self.joints:
            name = clean_name(self.nodes[idx].get("name", ""))
            if not name or name in self.bones:
                raise ValueError("Missing or duplicate bone name")
            self.bones[name] = idx
        if "Hips" not in self.bones:
            raise ValueError("Expected a named Hips joint")
        self.names = {v:k for k,v in self.bones.items()}
        self.order = []
        seen = set()
        def walk(idx):
            if idx in seen:
                raise ValueError("Repeated/cyclic scene node")
            seen.add(idx)
            self.order.append(idx)
            for child in self.nodes[idx].get("children", []):
                walk(child)
        for root in doc["scenes"][doc.get("scene", 0)]["nodes"]:
            walk(root)
        if not self.joints.issubset(seen):
            raise ValueError("Skin joints are outside the selected scene")
        self.rest_local = {}
        for idx in self.order:
            n = self.nodes[idx]
            self.rest_local[idx] = decompose(n["matrix"]) if "matrix" in n else (
                tuple(n.get("translation", (0,0,0))),
                tuple(n.get("rotation", (0,0,0,1))), tuple(n.get("scale", (1,1,1))))
        self.rest_world = self.world({})
        self.rest_world_trs = {i:decompose(m) for i,m in self.rest_world.items()}

    def world(self, changes):
        result = {}
        for idx in self.order:
            p,q,s = self.rest_local[idx]
            edit = changes.get(idx, {})
            local = compose(edit.get("p",p), edit.get("q",q), s)
            result[idx] = mmul(result.get(self.parent.get(idx), IDENTITY), local)
        return result


class Canonical:
    def __init__(self, index_path, binary_path):
        self.doc = json.loads(Path(index_path).read_text())
        if self.doc.get("schema") != 2:
            raise ValueError("Expected canonical schema 2")
        layout = self.doc["layout"]
        nt,np,nr = (int(layout[k]) for k in ("times","pos","rot"))
        raw = Path(binary_path).read_bytes()
        if len(raw) != 4*(nt+np)+2*nr:
            raise ValueError("Canonical binary does not match index layout")
        self.times = struct.unpack_from(f"<{nt}f", raw, 0)
        self.pos = struct.unpack_from(f"<{np}f", raw, nt*4)
        self.rot = struct.unpack_from(f"<{nr}h", raw, (nt+np)*4)

    def sample(self, clip_id, seconds):
        clip = self.doc["clips"][clip_id]
        count,start = clip["frames"],clip["time"]
        ts = self.times[start:start+count]
        if len(ts) != count or count < 2 or any(b <= a for a,b in zip(ts,ts[1:])):
            raise ValueError(f"Invalid clip time samples: {clip_id}")
        hi = bisect.bisect_right(ts, seconds)
        lo,hi = (0,0) if hi == 0 else ((count-1,count-1) if hi >= count else (hi-1,hi))
        f = 0. if hi == lo else (seconds-ts[lo])/(ts[hi]-ts[lo])
        out = {}
        for bone,kind,offset in clip["tracks"]:
            if kind not in ("p","q"):
                raise ValueError(f"Unknown track kind {kind}")
            n,array = (3,self.pos) if kind == "p" else (4,self.rot)
            if offset < 0 or offset+count*n > len(array):
                raise ValueError("Canonical track exceeds buffer")
            divisor = 1. if kind == "p" else 32767.
            a = tuple(array[offset+lo*n+k]/divisor for k in range(n))
            b = tuple(array[offset+hi*n+k]/divisor for k in range(n))
            value = tuple(x+(y-x)*f for x,y in zip(a,b)) if kind == "p" else qslerp(a,b,f)
            out.setdefault(clean_name(bone), {})[kind] = value
        return out


def to_ue_cm(p):
    return [p[2]*100, -p[0]*100, p[1]*100]


class Retargeter:
    def __init__(self, source, target, library, ground_lift=True, hold_pass_opening_sample=False):
        self.source,self.target,self.library = source,target,library
        self.ground_lift = ground_lift
        if not isinstance(hold_pass_opening_sample, bool):
            raise ValueError("Pass opening hold must be an explicit boolean option")
        self.derived_corrections = ([pass_opening_hold_record(library)]
                                    if hold_pass_opening_sample else [])
        self.leg_ratio = (target.rest_world_trs[target.bones["Hips"]][0][1]
                          /source.rest_world_trs[source.bones["Hips"]][0][1])
        if not .25 < self.leg_ratio < 4:
            raise ValueError("Unexpected rest-height ratio. Check rig units.")
        self.fixed_fingers, self.ignored_terminal_fingers = prepare_fixed_fingers(source, target, library)
        self.idle_hips = library.sample("idle",0)["Hips"]["p"]

    def _source_sample(self, clip_id, seconds):
        # One sampling path prevents a corrected body from retaining the old
        # opening root jump. Canonical.sample and its source arrays stay raw.
        if clip_id == "pass" and self.derived_corrections:
            held_time = self.derived_corrections[0]["source_sample_time_seconds"]
            if 0.0 <= seconds < held_time:
                seconds = held_time
        return self.library.sample(clip_id, seconds)

    def raw_hips_world(self, clip_id, seconds):
        """World hips before planar anchoring, under explicit sampling corrections."""
        pose = self._source_sample(clip_id, seconds)
        idx = self.source.bones["Hips"]
        p = pose["Hips"]["p"]
        return point(self.source.rest_world.get(self.source.parent.get(idx),IDENTITY),p)

    def sample(self, clip_id, seconds):
        pose = self._source_sample(clip_id, seconds)
        changes = {}
        for name,idx in self.source.bones.items():
            entry = pose.get(name,{})
            q = entry.get("q", self.fixed_fingers.get(name))
            if q is not None:
                # The fixed finger pose follows the same unit-rotation rule as
                # interpolated tracks before it enters a world matrix.
                changes.setdefault(idx,{})["q"] = qnorm(q)
            if name == "Hips":
                p = entry.get("p",self.source.rest_local[idx][0])
                changes.setdefault(idx,{})["p"] = (self.idle_hips[0],p[1],self.idle_hips[2])
        sw = self.source.world(changes)
        target_changes,desired_world = {},{}
        for idx in self.target.order:
            if idx not in self.target.joints:
                continue
            name = self.target.names[idx]
            parent = self.target.parent.get(idx)
            rest_parent_q = self.target.rest_world_trs[parent][1] if parent is not None else (0,0,0,1)
            parent_q = desired_world.get(parent,rest_parent_q)
            own_rest_q = self.target.rest_world_trs[idx][1]
            if name in self.source.bones:
                si = self.source.bones[name]
                sq = decompose(sw[si])[1]
                rest_sq = self.source.rest_world_trs[si][1]
                wq = qmul(qmul(sq,qconj(rest_sq)),own_rest_q)
            else:
                wq = qmul(parent_q,qmul(qconj(rest_parent_q),own_rest_q))
            target_changes[idx] = {"q": qnorm(qmul(qconj(parent_q),wq))}
            desired_world[idx] = wq
        sh,th = self.source.bones["Hips"],self.target.bones["Hips"]
        displacement = tuple((sw[sh][12+k]-self.source.rest_world[sh][12+k])*self.leg_ratio for k in range(3))
        desired_hip = tuple(self.target.rest_world[th][12+k]+displacement[k] for k in range(3))
        tw = self.target.world(target_changes)
        parent_inv = inverse_affine(tw.get(self.target.parent.get(th),IDENTITY))
        target_changes[th]["p"] = point(parent_inv,desired_hip)
        tw = self.target.world(target_changes)
        lift = 0.
        if self.ground_lift:
            feet = [self.target.bones[n] for n in ("LeftFoot","RightFoot","LeftToeBase","RightToeBase")]
            lift = max(0.,-min(tw[i][13]-self.target.rest_world[i][13] for i in feet))
            if lift:
                desired_hip = (desired_hip[0],desired_hip[1]+lift,desired_hip[2])
                target_changes[th]["p"] = point(parent_inv,desired_hip)
                tw = self.target.world(target_changes)
        return target_changes,tw,lift


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def prepare_clip(retargeter, target_doc, target_binary, clip_id, fps, glb_path):
    meta = retargeter.library.doc["meta"][clip_id]
    sample_window = clip_sample_window(retargeter.library, clip_id)
    duration = float(meta["duration"])
    intervals = max(1,math.ceil(duration*fps-1e-8))
    times = [min(i/fps,duration) for i in range(intervals+1)]
    corrections = [copy.deepcopy(record) for record in retargeter.derived_corrections
                   if record["animation_id"] == clip_id]
    # An explicit boundary key preserves the hold in the prepared GLB and root
    # descriptor. The later FBX bake still uses its declared frame sampling rate.
    if corrections:
        times = sorted(set(times + [record["source_sample_time_seconds"] for record in corrections]))
    target = retargeter.target
    rotations = {i:[] for i in target.joints}
    hips,roots,lifts = [],[],[]
    contact = float(meta["contact"])*duration if "contact" in meta else None
    raw_anchor = retargeter.raw_hips_world(clip_id,contact if contact is not None else 0)
    for t in times:
        changes,world,lift = retargeter.sample(clip_id,t)
        for idx in rotations:
            q = changes[idx]["q"]
            if rotations[idx] and sum(a*b for a,b in zip(rotations[idx][-4:],q)) < 0:
                q = tuple(-v for v in q)
            rotations[idx].extend(q)
        hips.extend(changes[target.bones["Hips"]]["p"])
        lifts.append(lift)
    root_times = set(times)
    root_times.add(intervals/fps)
    if contact is not None:
        root_times.add(contact)
    for t in sorted(root_times):
        raw = retargeter.raw_hips_world(clip_id,min(t,duration))
        planar = ((raw[0]-raw_anchor[0])*retargeter.leg_ratio,0.,
                  (raw[2]-raw_anchor[2])*retargeter.leg_ratio)
        roots.append({"time_seconds":t,"position_cm":to_ue_cm(planar)})
    doc = copy.deepcopy(target_doc)
    for idx,name in target.names.items():
        doc["nodes"][idx]["name"] = name
        if "matrix" in doc["nodes"][idx]:
            p,q,s = target.rest_local[idx]
            del doc["nodes"][idx]["matrix"]
            doc["nodes"][idx].update(translation=p,rotation=q,scale=s)
    binary = bytearray(target_binary)
    def accessor(values,n,type_name):
        binary.extend(b"\0"*((-len(binary))%4))
        offset = len(binary)
        binary.extend(struct.pack(f"<{len(values)}f",*values))
        views = doc.setdefault("bufferViews",[])
        vi = len(views)
        views.append({"buffer":0,"byteOffset":offset,"byteLength":len(values)*4})
        acc = {"bufferView":vi,"componentType":5126,"count":len(values)//n,"type":type_name}
        if n == 1:
            acc.update(min=[min(values)],max=[max(values)])
        accessors = doc.setdefault("accessors",[])
        ai = len(accessors)
        accessors.append(acc)
        return ai
    ti = accessor(times,1,"SCALAR")
    animation = {"name":f"A_{clip_id.title()}","samplers":[],"channels":[]}
    def channel(idx,path,values,n,typ):
        si = len(animation["samplers"])
        animation["samplers"].append({"input":ti,"output":accessor(values,n,typ),"interpolation":"LINEAR"})
        animation["channels"].append({"sampler":si,"target":{"node":idx,"path":path}})
    for idx in sorted(rotations):
        channel(idx,"rotation",rotations[idx],4,"VEC4")
    channel(target.bones["Hips"],"translation",hips,3,"VEC3")
    doc["animations"] = [animation]
    write_glb(glb_path,doc,binary)
    result = {
        "id":clip_id,"name":animation["name"],"source_duration_seconds":duration,
        "duration_seconds":intervals/fps,"fps":fps,"frames":intervals+1,
        "contact_seconds":contact,"loop":bool(meta.get("loop")),
        "derived_corrections":corrections,
        "source_meta":meta,"source_leg_ratio":retargeter.leg_ratio,
        "source_sample_window":sample_window,
        "ignored_source_only_terminal_finger_placeholders":retargeter.ignored_terminal_fingers,
        "planar_hips":"removed_from_clip","root_keys":roots,
        "root_keys_anchor":"contact" if contact is not None else "first_frame",
        "root_keys_coordinate_basis":"unreal-z-up-x-forward",
        "root_motion_baked_into_skeleton":False,
        "ground_lift_baked":retargeter.ground_lift,"maximum_ground_lift_cm":max(lifts)*100,
        "duration_padding_seconds":intervals/fps-duration,
        "surface_contact_validation":"unmeasured",
        "fbx_axes_validation":"pending_unreal_import"
    }
    if contact is not None:
        _,world,_ = retargeter.sample(clip_id,contact)
        foot = ("Left" if meta.get("foot") == "left" else "Right")+"Foot"
        toe = foot.replace("Foot","ToeBase")
        fp,tp = to_ue_cm(world[target.bones[foot]][12:15]),to_ue_cm(world[target.bones[toe]][12:15])
        result["contact_candidate"] = {
            "bone":foot,"toe_bone":toe,"foot_bone_position_cm":fp,"toe_bone_position_cm":tp,
            "candidate_ball_center_cm":[max(fp[0],tp[0])+11,(fp[1]+tp[1])/2,11],
            "candidate_rule":"Forward of foot/toe bones by ball radius, at ground ball height. Heuristic, not a measured mesh-surface socket.",
            "coordinate_basis":"unreal-x-forward-y-right-z-up-cm",
            "validation_status":"unmeasured"
        }
    return result


def self_test():
    def close(a,b,tol=1e-8):
        assert max(abs(x-y) for x,y in zip(a,b)) < tol,(a,b)
    q = qnorm((.2,-.3,.1,.8))
    m = compose((2,3,4),q,(.01,.01,.01))
    p,qr,s = decompose(m)
    close(p,(2,3,4)); close(s,(.01,.01,.01))
    assert abs(abs(sum(x*y for x,y in zip(q,qr)))-1) < 1e-8
    close(point(inverse_affine(m),point(m,(7,8,9))),(7,8,9))
    close(mmul(m,inverse_affine(m)),IDENTITY)
    close(qslerp(q,tuple(-x for x in q),.5),q)
    close(to_ue_cm((1,2,3)),(300,-100,200))
    # Rest-to-rest transfer must preserve the destination orientation.
    sr = qnorm((.1,.3,.2,.9)); tr = qnorm((-.2,.1,.4,.8))
    close(qmul(qmul(sr,qconj(sr)),tr),tr)
    # A centimetre-scaled source rig must transfer hip displacement in metres,
    # remove its horizontal travel from the clip, and apply target proportions.
    def test_rig(scale,height):
        return Rig({"nodes":[
            {"name":"Armature","scale":[scale]*3,"children":[1]},
            {"name":"mixamorig:Hips","translation":[0,height,0],"children":[2,4]},
            {"name":"mixamorig:LeftFoot","translation":[-.1/scale,-height,0],"children":[3]},
            {"name":"mixamorig:LeftToeBase","translation":[0,0,.15/scale]},
            {"name":"mixamorig:RightFoot","translation":[.1/scale,-height,0],"children":[5]},
            {"name":"mixamorig:RightToeBase","translation":[0,0,.15/scale]}],
            "scenes":[{"nodes":[0]}],"skins":[{"joints":[1,2,3,4,5]}]})
    class TestLibrary:
        doc = {"fingers":{}}
        def sample(self,clip,seconds):
            return {"Hips":{"p":(0,100,0) if clip == "idle" else (25,120,50),"q":(0,0,0,1)}}
    src,dst = test_rig(.01,100),test_rig(1,.8)
    r = Retargeter(src,dst,TestLibrary(),False)
    changes,world,_ = r.sample("pass",0)
    close(world[dst.bones["Hips"]][12:15],(0,.96,0))
    close(r.raw_hips_world("pass",0),(.25,1.2,.5))
    print("PASS: quaternion transfer, interpolation, affine inverse, unit-scaled hip retarget, planar separation and basis invariants")


if __name__ == "__main__":
    self_test()
