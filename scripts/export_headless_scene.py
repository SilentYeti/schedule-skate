"""Export static scene collider transforms for the shared headless cropper (no game launch or save writes)."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import tempfile

import numpy as np
import UnityPy
from scipy.spatial.transform import Rotation
from export_collision import mesh_blob,PROJECT

def export(game,cache,destination,scene,centre,terrain_radius):
    game=game.resolve(); cache=cache.resolve(); destination=destination.resolve()
    if destination.exists(): raise FileExistsError(f"Refusing to replace {destination}")
    if destination.is_relative_to(game) or game.is_relative_to(destination): raise ValueError("Export outside the game directory")
    document=json.loads((cache/"index.json").read_text())
    with (game/"GameAssembly.dll").open("rb") as stream:
        if hashlib.file_digest(stream,"sha512").hexdigest().upper()!=document["GameAssemblyHash"]:
            raise ValueError("Collision cache does not match this game build")
    def signature(name,vertices,counts,box):
        return (name,vertices,tuple(counts),tuple(box))
    meshes={}
    for entry in document["meshes"]:
        key=signature(entry["name"],entry["vertex_count"],entry["index_counts"],entry["bounds"])
        if key in meshes and meshes[key]["file"]!=entry["file"]: raise ValueError("Ambiguous mesh signature")
        meshes[key]=entry
    data=game/"Schedule I_Data"
    if scene not in ("level0","level1","level2"): raise ValueError("Select a shipped level0..level2 scene")
    env=UnityPy.Environment(path=str(data)); env.load_file(str(data/scene))
    worlds={}; active={}; transforms={}; dynamic={}
    def identity(obj): return (obj.assets_file.name,obj.path_id)
    def transform_for(go_pointer):
        go_obj=go_pointer.deref(); key=identity(go_obj)
        if key not in transforms:
            go=go_obj.parse_as_object()
            transforms[key]=next(c.component.deref() for c in go.m_Component if c.component.deref().type.name=="Transform")
        return transforms[key]
    def world(obj):
        key=identity(obj)
        if key in worlds: return worlds[key]
        t=obj.parse_as_object(); g=t.m_GameObject.deref_parse_as_object()
        xyz=lambda v:np.array([v.x,v.y,v.z],dtype=float)
        q=t.m_LocalRotation
        matrix=np.eye(4); matrix[:3,:3]=Rotation.from_quat([q.x,q.y,q.z,q.w]).as_matrix()*xyz(t.m_LocalScale)
        matrix[:3,3]=xyz(t.m_LocalPosition)
        parent=t.m_Father.deref() if t.m_Father.m_PathID else None
        if parent: matrix=world(parent)@matrix
        active[key]=bool(g.m_IsActive) and (active[identity(parent)] if parent else True)
        moving=False
        for c in g.m_Component:
            component=c.component.deref()
            if component.type.name=="Rigidbody": moving=not component.parse_as_object().m_IsKinematic
        dynamic[key]=moving or (dynamic[identity(parent)] if parent else False)
        worlds[key]=matrix
        return matrix
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".headless-scene-",dir=destination.parent) as temporary:
        stage=Path(temporary)/"scene"; stage.mkdir()
        instances=[]; unsupported={}
        for obj in list(env.objects):
            if obj.assets_file.name!=scene or obj.type.name not in ("MeshCollider","BoxCollider","SphereCollider","CapsuleCollider","TerrainCollider"): continue
            collider=obj.parse_as_object()
            if not collider.m_Enabled or collider.m_IsTrigger: continue
            t=transform_for(collider.m_GameObject); matrix=world(t)
            if not active[identity(t)] or dynamic[identity(t)]: continue
            go=collider.m_GameObject.deref_parse_as_object()
            if obj.type.name=="MeshCollider":
                if not collider.m_Mesh.m_PathID: continue
                mesh=collider.m_Mesh.deref_parse_as_object(); box=mesh.m_LocalAABB
                bounds=[box.m_Center.x,box.m_Center.y,box.m_Center.z,box.m_Extent.x,box.m_Extent.y,box.m_Extent.z]
                key=signature(mesh.m_Name,mesh.m_VertexData.m_VertexCount,[s.indexCount for s in mesh.m_SubMeshes],bounds)
                entry=meshes[key]; filename=entry["convex_file"] if collider.m_Convex else entry["file"]
                if not filename: raise ValueError("Missing convex geometry")
                if not (stage/filename).exists(): shutil.copyfile(cache/filename,stage/filename)
            elif obj.type.name=="BoxCollider":
                box_centre=np.array([collider.m_Center.x,collider.m_Center.y,collider.m_Center.z])
                half=np.array([collider.m_Size.x,collider.m_Size.y,collider.m_Size.z])*.5
                vertices=[box_centre+half*np.array([-1 if i&bit==0 else 1 for bit in (1,2,4)]) for i in range(8)]
                indices=[0,4,6,0,6,2,1,3,7,1,7,5,0,1,5,0,5,4,2,6,7,2,7,3,0,2,3,0,3,1,4,5,7,4,7,6]
                blob=mesh_blob(vertices,indices); filename=hashlib.sha256(blob).hexdigest()+".mesh"
                (stage/filename).write_bytes(blob)
            elif obj.type.name=="TerrainCollider":
                terrain=collider.m_TerrainData.deref_parse_as_object().m_Heightmap
                n=terrain.m_Resolution
                scale=terrain.m_Scale
                heights=np.asarray(terrain.m_Heights,dtype=np.int32).reshape(n,n)
                if heights.min()<0 or heights.max()>32767: raise ValueError("Unexpected serialized terrain height range")
                local=np.linalg.inv(matrix)@np.array([*centre,1.0])
                # Bound terrain to a test window; exporting the entire 2049-square heightmap is unnecessary.
                reach=np.linalg.norm(np.linalg.inv(matrix)[:3,:3],axis=1)*terrain_radius
                x0=max(0,int(np.floor((local[0]-reach[0])/scale.x))); x1=min(n-1,int(np.ceil((local[0]+reach[0])/scale.x)))
                z0=max(0,int(np.floor((local[2]-reach[2])/scale.z))); z1=min(n-1,int(np.ceil((local[2]+reach[2])/scale.z)))
                if x1<=x0 or z1<=z0: continue
                xs,zs=np.meshgrid(np.arange(x0,x1+1),np.arange(z0,z1+1))
                # shortcut: serialized 15-bit heights are inferred; exact runtime captures avoid this assumption.
                vertices=np.stack([xs*scale.x,heights[z0:z1+1,x0:x1+1]/32767*scale.y,zs*scale.z],axis=-1).reshape(-1,3)
                width=x1-x0+1
                base=(np.arange(z1-z0)[:,None]*width+np.arange(x1-x0)[None,:]).reshape(-1)
                indices=np.stack([base,base+width,base+width+1,base,base+width+1,base+1],axis=-1).reshape(-1)
                blob=mesh_blob(vertices,indices); filename=hashlib.sha256(blob).hexdigest()+".mesh"
                (stage/filename).write_bytes(blob)
            else:
                unsupported[obj.type.name]=unsupported.get(obj.type.name,0)+1; continue
            instances.append({"name":go.m_Name,"layer":go.m_Layer,"file":filename,"matrix":matrix.flatten(order="F").tolist()})
        report={"version":1,"GameAssemblyHash":document["GameAssemblyHash"],"scene":scene,
                "limitations":"Baked active static meshes, boxes and local terrain; no runtime state or ground layer filtering. Terrain height decoding is inferred; round primitives omitted.",
                "terrain_window":{"centre":centre,"radius":terrain_radius,"height_divisor":32767},
                "omitted":unsupported,"instances":instances}
        (stage/"scene.json").write_text(json.dumps(report,separators=(",",":"))+"\n")
        stage.rename(destination)
    print(f"Exported {len(instances)} static instances; omitted {unsupported}. Fixture: {destination/'scene.json'}")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",type=Path,default=Path.home()/".local/share/Steam/steamapps/common/Schedule I")
    parser.add_argument("--cache",type=Path,default=PROJECT/"data/schedule-collision")
    parser.add_argument("--out",type=Path,default=PROJECT/"data/headless-scene")
    parser.add_argument("--scene",choices=("level0","level1","level2"),default="level1")
    parser.add_argument("--centre",nargs=3,type=float,default=[0,0,0])
    parser.add_argument("--terrain-radius",type=float,default=48)
    args=parser.parse_args()
    if not np.isfinite(args.centre).all() or not 24<=args.terrain_radius<=64: parser.error("Finite centre; terrain-radius: 24..64")
    export(args.game,args.cache,args.out,args.scene,args.centre,args.terrain_radius)
