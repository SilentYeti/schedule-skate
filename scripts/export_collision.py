"""Export Schedule I collider meshes locally, including meshes discarded from CPU memory in-game."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import tempfile

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler
from scipy.spatial import ConvexHull, QhullError

PROJECT=Path(__file__).resolve().parents[1]

def triangle_indices(submeshes,triangles,vertex_count):
    indices=[]
    for submesh,faces in zip(submeshes,triangles,strict=True):
        base=submesh.baseVertex or 0
        for face in faces:
            if len(face)!=3:
                raise ValueError("Collision faces must have three vertices.")
            indices.extend(int(index)+base for index in face)
    if any(index<0 or index>=vertex_count for index in indices):
        raise ValueError("Collision index outside the vertex buffer.")
    return np.array(indices,dtype="<i4")

def mesh_blob(vertices,indices):
    vertices=np.asarray(vertices,dtype="<f4")
    indices=np.asarray(indices,dtype="<i4").reshape(-1)
    if vertices.ndim!=2 or vertices.shape[1]!=3 or not np.isfinite(vertices).all():
        raise ValueError("Invalid collision vertices.")
    if len(indices)%3 or (len(indices) and (indices.min()<0 or indices.max()>=len(vertices))):
        raise ValueError("Invalid collision triangles.")
    return struct.pack("<4sII",b"SKM1",len(vertices),len(indices))+vertices.tobytes()+indices.tobytes()

def convex_indices(vertices):
    hull=ConvexHull(vertices)
    faces=hull.simplices.copy()
    for face,equation in zip(faces,hull.equations,strict=True):
        a,b,c=vertices[face]
        if np.dot(np.cross(b-a,c-a),equation[:3])<0:
            face[1],face[2]=face[2],face[1]
    return faces.reshape(-1)

def export(game,destination):
    game=game.resolve()
    destination=destination.resolve()
    data=game/"Schedule I_Data"
    if not (game/"GameAssembly.dll").is_file() or not data.is_dir():
        raise ValueError("Select the installed Schedule I game directory.")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing cache: {destination}")
    if destination==game or destination.is_relative_to(game) or game.is_relative_to(destination):
        raise ValueError("Export into the project, separately from the installed game.")
    env=UnityPy.Environment(path=str(data))
    for asset in sorted(data.iterdir()):
        if asset.suffix==".assets" or asset.name.startswith("level") and asset.suffix=="":
            env.load_file(str(asset))
    wanted={}
    collider_count=0
    for obj in list(env.objects):
        if obj.type.name!="MeshCollider":
            continue
        collider=obj.parse_as_object()
        if not collider.m_Mesh.m_PathID:
            continue
        obj=collider.m_Mesh.deref()
        key=(obj.assets_file.name,obj.path_id)
        if key not in wanted:
            wanted[key]=(obj,set())
        wanted[key][1].add(bool(collider.m_Convex))
        collider_count+=1
    print(f"Exporting {len(wanted)} meshes used by {collider_count} colliders",flush=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".collision-export-",dir=destination.parent) as temporary:
        stage=Path(temporary)/"cache"
        stage.mkdir()
        entries=[]
        planar_convex=[]
        for number,(obj,flags) in enumerate(wanted.values()):
            mesh=obj.parse_as_object()
            handler=MeshHandler(mesh)
            handler.process()
            vertices=np.asarray(handler.m_Vertices,dtype="<f4")
            if not len(vertices):
                continue
            counts=[submesh.indexCount for submesh in mesh.m_SubMeshes]
            indices=triangle_indices(mesh.m_SubMeshes,handler.get_triangles(),len(vertices))
            if not len(indices):
                continue
            blob=mesh_blob(vertices,indices)
            filename=hashlib.sha256(blob).hexdigest()+".mesh"
            (stage/filename).write_bytes(blob)
            box=mesh.m_LocalAABB
            record={"name":mesh.m_Name,"vertex_count":len(vertices),"index_counts":counts,
                "bounds":[box.m_Center.x,box.m_Center.y,box.m_Center.z,box.m_Extent.x,box.m_Extent.y,box.m_Extent.z],
                "file":filename,"convex_file":None}
            if True in flags:
                try:
                    convex=mesh_blob(vertices,convex_indices(vertices))
                    record["convex_file"]=hashlib.sha256(convex).hexdigest()+".mesh"
                    (stage/record["convex_file"]).write_bytes(convex)
                except QhullError:
                    # shortcut: degenerate planar hulls retain source faces; model PhysX extrusion if needed.
                    record["convex_file"]=filename
                    planar_convex.append(mesh.m_Name)
            entries.append(record)
            if number%100==0:
                print(f"{number+1}/{len(wanted)}: {mesh.m_Name}",flush=True)
        with (game/"GameAssembly.dll").open("rb") as stream:
            game_hash=hashlib.file_digest(stream,"sha512").hexdigest().upper()
        document={"version":1,"GameAssemblyHash":game_hash,"meshes":entries,
                  "colliders":collider_count,"planar_convex":planar_convex}
        (stage/"index.json").write_text(json.dumps(document,separators=(",",":"))+"\n")
        stage.rename(destination)
    print(f"Exported {len(entries)} collider meshes to {destination}",flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",type=Path,default=Path.home()/".local/share/Steam/steamapps/common/Schedule I")
    parser.add_argument("--out",type=Path,default=PROJECT/"data/schedule-collision")
    args=parser.parse_args()
    try:
        export(args.game,args.out)
    except (OSError,ValueError) as error:
        parser.exit(1,f"Collision export failed: {error}\n")

if __name__=="__main__":
    main()
