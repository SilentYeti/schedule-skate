"""Extract a baked body rig for headless retargeting checks; never launch the game or alter saves."""
from pathlib import Path
import argparse
import json
import UnityPy

PROJECT=Path(__file__).resolve().parents[1]

def export(game,out,renderer_name):
    game=game.resolve(); out=out.resolve()
    if out.exists(): raise FileExistsError(f"Refusing to replace {out}")
    if out.is_relative_to(game) or game.is_relative_to(out): raise ValueError("Export outside the game directory")
    data=game/"Schedule I_Data"
    env=UnityPy.Environment(path=str(data))
    for asset in data.glob("*.assets"): env.load_file(str(asset))
    for obj in list(env.objects):
        if obj.type.name!="SkinnedMeshRenderer": continue
        renderer=obj.parse_as_object()
        if renderer.m_GameObject.deref_parse_as_object().m_Name!=renderer_name: continue
        bones=[]
        for pointer in renderer.m_Bones:
            t=pointer.deref_parse_as_object()
            q=t.m_LocalRotation; p=t.m_LocalPosition
            parent=t.m_Father.deref_parse_as_object().m_GameObject.deref_parse_as_object().m_Name if t.m_Father.m_PathID else None
            bones.append({"name":t.m_GameObject.deref_parse_as_object().m_Name,"parent":parent,
                          "position":[p.x,p.y,p.z],"rotation":[q.x,q.y,q.z,q.w]})
        if not bones or len({b["name"] for b in bones})!=len(bones): raise ValueError("Body rig has missing or duplicate bones")
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps({"renderer":renderer_name,"file":obj.assets_file.name,"id":obj.path_id,"bones":bones},indent=2)+"\n")
        print(f"Exported {len(bones)} body bones to {out}")
        return
    raise ValueError(f"No body renderer named {renderer_name}")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",type=Path,default=Path.home()/".local/share/Steam/steamapps/common/Schedule I")
    parser.add_argument("--out",type=Path,default=PROJECT/"data/avatar-target-fixture.json")
    parser.add_argument("--renderer",default="Body_LOD3")
    args=parser.parse_args()
    try: export(args.game,args.out,args.renderer)
    except (OSError,ValueError) as error: parser.exit(1,f"Avatar fixture export failed: {error}\n")
