using System.Security.Cryptography;
using Newtonsoft.Json;
using UnityEngine;

namespace SkateSchedule;

static class CollisionCache
{
    private static CacheIndex? index;
    private static Dictionary<string,CollisionGeometry> loaded=new();
    private sealed class CacheIndex
    {
        public int version {get;set;}
        public string GameAssemblyHash {get;set;}="";
        public Entry[] meshes=Array.Empty<Entry>();
    }
    private sealed class Entry
    {
        public string name="";
        public int vertex_count {get;set;}
        public int[] index_counts=Array.Empty<int>();
        public float[] bounds=Array.Empty<float>();
        public string file="";
        public string? convex_file {get;set;}
    }
    // File validation and all buffer reads run before skating, on a worker; Get only matches Unity metadata.
    public static void Initialize(string gameRoot)
    {
        string directory=Path.Combine(gameRoot,"Mods","SkateScheduleAssets");
        string path=Path.Combine(directory,"index.json");
        if (!File.Exists(path))
            throw new IOException("Collision cache is missing. Run export_collision.py and install_mod.py --update-mod.");
        var candidate=JsonConvert.DeserializeObject<CacheIndex>(File.ReadAllText(path));
        if (candidate==null || candidate.version!=1) throw new IOException("Unsupported collision cache.");
        using (var stream=File.OpenRead(Path.Combine(gameRoot,"GameAssembly.dll")))
        using (var hash=SHA512.Create())
        {
            if (Convert.ToHexString(hash.ComputeHash(stream))!=candidate.GameAssemblyHash)
                throw new IOException("Schedule I changed. Re-export its collision cache.");
        }
        var buffers=new Dictionary<string,CollisionGeometry>();
        foreach (var filename in candidate.meshes.SelectMany(e=>new[]{e.file,e.convex_file}).Where(f=>f!=null).Distinct())
        {
            if (string.IsNullOrEmpty(filename) || Path.GetFileName(filename)!=filename)
                throw new IOException("Unsafe collision cache filename.");
            buffers.Add(filename,CollisionGeometry.Read(Path.Combine(directory,filename)));
        }
        loaded=buffers;
        index=candidate;
    }
    public static CollisionGeometry Get(Mesh mesh,bool convex)
    {
        if (index==null) throw new IOException("Collision cache is not ready.");
        var box=mesh.bounds;
        var bounds=new[]{box.center.x,box.center.y,box.center.z,box.extents.x,box.extents.y,box.extents.z};
        var counts=Enumerable.Range(0,mesh.subMeshCount).Select(i=>(int)mesh.GetIndexCount(i)).ToArray();
        string name=mesh.name;
        if (name.EndsWith(" (Clone)",StringComparison.Ordinal)) name=name[..^8];
        var candidates=index.meshes.Where(e=>e.name==name && e.vertex_count==mesh.vertexCount &&
            e.index_counts.SequenceEqual(counts) && e.bounds.Length==6).ToArray();
        // Imported assets preserve their exact bounds; similarly named road variants can differ by tiny amounts.
        var precise=candidates.Where(e=>e.bounds.SequenceEqual(bounds)).ToArray();
        var selected=precise.Length>0?precise:candidates.Where(e=>
            e.bounds.Select((v,i)=>Math.Abs(v-bounds[i])<=1e-4f*Math.Max(1,Math.Abs(v))).All(v=>v));
        var matches=selected.Select(e=>convex?e.convex_file:e.file).Distinct().ToArray();
        if (matches.Length!=1 || matches[0]==null)
            throw new IOException($"No unique cached collision for {name} ({mesh.vertexCount} vertices, convex={convex}).");
        return loaded[matches[0]!];
    }
}
