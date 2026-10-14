using System.Numerics;
using SkateSchedule;

string failureFixture=Path.Combine(Path.GetTempPath(),"skate-failure-check-"+Guid.NewGuid());
try
{
    Directory.CreateDirectory(failureFixture);
    for (int i=0;i<10;i++)
    {
        string path=Path.Combine(failureFixture,$"collision-{i}.skb");
        File.WriteAllText(path,$"world {i}"); File.SetLastWriteTimeUtc(path,DateTime.UnixEpoch.AddSeconds(i));
    }
    File.WriteAllText(Path.Combine(failureFixture,"runtime.jsonl"),"trace");
    FailureCapture.Save(failureFixture,"request","response");
    string saved=Path.Combine(failureFixture,"latest-failure");
    if (Directory.GetFiles(saved,"*.skb").Length!=8 || File.Exists(Path.Combine(saved,"collision-0.skb")) ||
        File.ReadAllText(Path.Combine(saved,"rejected-response.json"))!="response") throw new Exception("Failure world retention failed.");
    foreach (string path in Directory.GetFiles(failureFixture,"*.skb")) File.Delete(path);
    FailureCapture.Save(failureFixture,"next request","next response");
    if (Directory.GetFiles(saved,"*.skb").Length!=0 || File.ReadAllText(Path.Combine(saved,"runtime.jsonl"))!="trace")
        throw new Exception("Failure capture retained stale worlds or lost its trace.");
    Console.WriteLine("Failure capture passed: bounded recent worlds, trace/response, replacement without stale geometry.");
}
finally {Directory.Delete(failureFixture,true);}

var mountGuard=new MountGuard();
var localMount=new IntPtr(1); var otherMount=new IntPtr(2);
if (!mountGuard.AllowRemoval(localMount)) throw new Exception("Native exit blocked outside equipment Update.");
var outsideUpdate=mountGuard.Begin(localMount,false);
try
{
    if (mountGuard.AllowRemoval(localMount) || !mountGuard.AllowRemoval(otherMount))
        throw new Exception("Flip protection did not stay local to the mounted player.");
    var outerUpdate=mountGuard.Begin(localMount,true);
    try {if (!mountGuard.AllowRemoval(localMount)) throw new Exception("Explicit skateboard dismount blocked.");}
    finally {mountGuard.End(outerUpdate);}
    if (mountGuard.AllowRemoval(localMount)) throw new Exception("Nested equipment callback lost flip protection.");
}
finally {mountGuard.End(outsideUpdate);}
if (!mountGuard.AllowRemoval(localMount)) throw new Exception("Exit remained blocked after callback cleanup.");
Console.WriteLine("Mount ownership passed: flips protected, explicit/unrelated exits allowed, nested scope restored.");

foreach (float half in new[]{0f,1.2f})
{
    const float radius=0.4f;
    var faces=RoundCollision.Capsule(radius,half).ToArray();
    if (faces.Length<200) throw new Exception("Missing capsule faces.");
    foreach (var (a,b,c) in faces)
    {
        foreach (var v in new[]{a,b,c})
        {
            var axis=new Vector3(0,Math.Clamp(v.Y,-half,half),0);
            if (Math.Abs(Vector3.Distance(v,axis)-radius)>1e-5f) throw new Exception("Incorrect capsule surface.");
        }
        var centre=(a+b+c)/3;
        var nearest=new Vector3(0,Math.Clamp(centre.Y,-half,half),0);
        if (Vector3.Dot(Vector3.Cross(b-a,c-a),centre-nearest)<=0) throw new Exception("Inward capsule face.");
    }
    float top=faces.SelectMany(f=>new[]{f.A.Y,f.B.Y,f.C.Y}).Max();
    float bottom=faces.SelectMany(f=>new[]{f.A.Y,f.B.Y,f.C.Y}).Min();
    if (Math.Abs(top-(half+radius))>1e-5 || Math.Abs(bottom+half+radius)>1e-5)
        throw new Exception("Incorrect capsule height.");
    Console.WriteLine($"{(half==0?"Sphere":"Capsule")}: {faces.Length} outward faces; surface and height checks passed.");
}
try
{
    _=RoundCollision.Capsule(float.NaN,0).ToArray();
    throw new Exception("Invalid radius accepted.");
}
catch (ArgumentOutOfRangeException) {}
Console.WriteLine("Round collider checks passed.");

Vector3 minimum=new Vector3(-1),maximum=new Vector3(1);
var a0=new Vector3(-10,0,-10); var b0=new Vector3(0,0,10); var c0=new Vector3(10,0,-10);
var polygon=CollisionClip.Polygon(a0,b0,c0,minimum,maximum);
float area=0;
for (int i=1;i<polygon.Count-1;i++)
{
    var normal=Vector3.Cross(polygon[i]-polygon[0],polygon[i+1]-polygon[0]);
    if (normal.Y<=0) throw new Exception("Clipping reversed floor winding.");
    area+=normal.Length()*0.5f;
}
if (Math.Abs(area-4)>1e-5) throw new Exception("Clipping did not preserve the floor inside the box.");
if (!CollisionClip.Outside(new Vector3(2,0,0),new Vector3(3,0,0),new Vector3(2,0,1),minimum,maximum))
    throw new Exception("Distant triangle was not excluded.");
var random=new Random(24);
Vector3 RandomPoint() => new((float)random.NextDouble()*10-5,(float)random.NextDouble()*10-5,(float)random.NextDouble()*10-5);
for (int attempt=0;attempt<100;attempt++)
{
    var a=RandomPoint(); var b=RandomPoint(); var c=RandomPoint();
    var original=Vector3.Cross(b-a,c-a);
    var result=CollisionClip.Polygon(a,b,c,minimum,maximum);
    foreach (var point in result)
        if (!CollisionClip.Inside(point,minimum-new Vector3(1e-5f),maximum+new Vector3(1e-5f)))
            throw new Exception("Clipped geometry escaped the box.");
    for (int i=1;i<result.Count-1;i++)
        if (Vector3.Dot(original,Vector3.Cross(result[i]-result[0],result[i+1]-result[0])) < -1e-5f)
            throw new Exception("Clipping reversed triangle winding.");
}
Console.WriteLine("Collision cropping passed: floor area, winding, distant faces and 100 triangle samples.");

var floorGeometry=new CollisionGeometry {Vertices=new[]{a0,b0,c0},Indices=new[]{0,1,2}};
var stationaryBoard=Vector3.Zero; var walkingRider=new Vector3(35,0,0);
var distantFloor=new[]{new CollisionInstance(floorGeometry,Matrix4x4.CreateTranslation(walkingRider))};
try
{
    CollisionCrop.GatherPacked(distantFloor,stationaryBoard,Vector3.Zero);
    throw new Exception("Detached-rider regression floor unexpectedly covered the stationary board.");
}
catch (InvalidOperationException) {}
foreach (var state in new[]{"BipedGround","BipedAir","OffBoardPushing","WipeoutGround","Teleporting"})
{
    var focus=CollisionFocus.Position(state,stationaryBoard,walkingRider);
    if (focus!=walkingRider || CollisionCrop.GatherPacked(distantFloor,focus,Vector3.Zero).Length==0)
        throw new Exception($"{state}: collision did not follow the detached skater.");
}
foreach (var state in new[]{"PhysicsGround","KnownAir","GrindBoardslide","LandingOnDeck"})
    if (CollisionFocus.Position(state,stationaryBoard,walkingRider)!=stationaryBoard)
        throw new Exception($"{state}: collision stopped following the ridden board.");
Console.WriteLine("Detached skater coverage passed: distant on-foot/bail floor retained, riding follows deck.");
foreach (var scale in new[]{1f,-1f})
{
    var matrix=Matrix4x4.CreateScale(scale,1,1)*Matrix4x4.CreateRotationY(0.7f)*Matrix4x4.CreateTranslation(100,5,-50);
    var origin=new Vector3(100,5,-50);
    var triangles=await Task.Run(()=>CollisionCrop.Gather(new[]{new CollisionInstance(floorGeometry,matrix)},origin,origin));
    foreach (var triangle in triangles)
    {
        Vector3 V(float[] v)=>new(v[0],v[1],v[2]);
        var normal=Vector3.Cross(V(triangle[1])-V(triangle[0]),V(triangle[2])-V(triangle[0]));
        if (normal.Y<=0 || triangle.SelectMany(v=>v).Any(v=>!float.IsFinite(v)))
            throw new Exception("Shared worker cropper reversed ground winding.");
        if (triangle.Any(v=>Math.Abs(v[1])>1e-5)) throw new Exception("Worker cropper did not subtract world origin.");
    }
}
try { CameraMath.Validate(new float[9]); throw new Exception("Degenerate camera accepted."); }
catch (IOException) {}
CameraMath.Validate(new float[]{1,0,0,0,1,0,0,0,1});
var placedCamera=CameraMath.Position(new float[]{2,3,4},new Vector3(100,5,-50),new Vector3(1.8f,0,0.7f));
if (Vector3.Distance(placedCamera,new Vector3(99.8f,8,-45.3f))>1e-5) throw new Exception("Camera/model calibration diverged.");
foreach (var direction in new[]{Vector3.UnitZ,Vector3.UnitX,-Vector3.UnitZ,-Vector3.UnitX})
{
    float heading=CameraMath.Heading(direction,Vector3.Zero);
    var engineForward=new Vector3(MathF.Sin(heading),0,MathF.Cos(heading));
    var reflected=new Vector3(-engineForward.X,engineForward.Y,engineForward.Z);
    if (Vector3.Dot(reflected,direction)<0.99999f) throw new Exception("Camera heading did not follow physical axles.");
}
Console.WriteLine("Worker geometry passed: rotation, translation, mirrored scale, winding and camera degeneracy.");

// This face has area in world space but its X extent rounds away after moving far from the session origin.
var sliver=new CollisionGeometry {Vertices=new[]{new Vector3(49,0,0),new Vector3(49+4e-6f,0,10),new Vector3(49+4e-6f,0,0),
    new Vector3(48,0,0),new Vector3(48,0,10),new Vector3(50,0,0)},Indices=new[]{0,1,2,3,4,5}};
if (Vector3.Cross(sliver.Vertices[1]-sliver.Vertices[0],sliver.Vertices[2]-sliver.Vertices[0]).LengthSquared()<1e-10f)
    throw new Exception("Regression fixture did not contain a valid world-space sliver.");
var transmitted=CollisionCrop.Gather(new[]{new CollisionInstance(sliver,Matrix4x4.Identity)},new Vector3(49,0,5),new Vector3(-150,0,0));
if (transmitted.Length!=1) throw new Exception("Origin subtraction emitted a degenerate collision sliver.");
Console.WriteLine("Moving-origin collision passed: collapsed sliver excluded, usable floor retained.");
var packed=CollisionCrop.GatherPacked(new[]{new CollisionInstance(sliver,Matrix4x4.Identity)},new Vector3(49,0,5),new Vector3(-150,0,0));
using (var reader=new BinaryReader(new MemoryStream(packed)))
{
    if (packed.Length!=transmitted.Length*36) throw new Exception("Packed collision count differs from JSON.");
    foreach (var triangle in transmitted) foreach (var vertex in triangle) foreach (float value in vertex)
        if (reader.ReadSingle()!=value) throw new Exception("Packed collision changed coordinates or winding.");
}
var terrain=new CollisionGeometry {Terrain=new TerrainSamples(new[]{0f,0f,0f,0f},2,2,4,7,0.5f,0.25f,100)};
terrain.Prepare();
if (terrain.Vertices[3]!=new Vector3(2.5f,0,2) || terrain.Indices.Length!=6 ||
    Vector3.Cross(terrain.Vertices[terrain.Indices[1]]-terrain.Vertices[0],terrain.Vertices[terrain.Indices[2]]-terrain.Vertices[0]).Y<=0)
    throw new Exception("Worker terrain construction changed its origin, spacing or winding.");
Console.WriteLine("Packed geometry and worker terrain buffers passed.");
float[] transform0={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
float[] transform1={-1,0,0,0,0,-1,0,0,0,0,1,0,10,2,4,1};
var halfway=new float[16]; PoseBlend.Matrix(transform0,transform1,0.5f,halfway);
if (Math.Abs(halfway[12]-5)>1e-6 || Math.Abs(halfway[13]-1)>1e-6 || Math.Abs(halfway[14]-2)>1e-6 ||
    Math.Abs(new Vector3(halfway[0],halfway[1],halfway[2]).Length()-1)>1e-6)
    throw new Exception("Half-turn interpolation collapsed the board or moved its centre incorrectly.");
var basis=new float[9]; PoseBlend.Basis(new[]{1f,0,0,0,1,0,0,0,1},new[]{-1f,0,0,0,-1,0,0,0,1},0.5f,basis);
CameraMath.Validate(basis);
Console.WriteLine("Pose interpolation passed: rotating deck retains unit axes and camera basis.");
var shearedPose=(float[])transform1.Clone(); shearedPose[4]=2;
if (PoseBlend.Matrix(transform0,shearedPose,0.5f,halfway) || !halfway.SequenceEqual(shearedPose))
    throw new Exception("Unsupported native transform stopped skating or was changed by interpolation fallback.");
Console.WriteLine("Native pose fallback passed: finite shear retained without stopping the session.");

var sourceBind=Quaternion.CreateFromAxisAngle(Vector3.UnitX,0.7f);
var targetBind=Quaternion.CreateFromAxisAngle(Vector3.UnitZ,-1.2f);
var motion=Quaternion.CreateFromAxisAngle(Vector3.UnitY,0.4f);
var neutral=RetargetMath.Rotation(sourceBind,sourceBind,targetBind);
var animated=RetargetMath.Rotation(motion*sourceBind,sourceBind,targetBind);
if (Math.Abs(Quaternion.Dot(neutral,targetBind))<0.99999f || Math.Abs(Quaternion.Dot(animated,motion*targetBind))<0.99999f)
    throw new Exception("Retargeting did not preserve neutral bind axes and world-space joint motion.");
var scaledHip=RetargetMath.HipPosition(new Vector3(100,5,50),new Vector3(2,1,4),new Vector3(2,0,4),RetargetMath.LegScale(1.2f,1));
if (Vector3.Distance(scaledHip,new Vector3(100,6.2f,50))>1e-5) throw new Exception("Retargeted hips did not follow the displayed board/proportions.");
var feetCorrection=RetargetMath.FootCorrection(new Vector3(-1,-0.07f,0),new Vector3(1,-0.07f,0),-Vector3.UnitX,Vector3.UnitX);
if (Vector3.Distance(feetCorrection,new Vector3(0,0.07f,0))>1e-6) throw new Exception("Retargeted feet did not compensate the hip offsets.");
foreach (float bad in new[]{0f,-1f,float.NaN,float.PositiveInfinity,20f})
{
    try {RetargetMath.LegScale(bad,1); throw new Exception("Invalid avatar proportions accepted.");}
    catch (IOException) {}
}
Console.WriteLine("Avatar math passed: calibrated bind axes, joint motion, board anchoring and leg proportions.");
foreach (var direction in new[]{Vector3.UnitX,-Vector3.UnitX,Vector3.Normalize(new Vector3(1,-1,0.4f))})
{
    var aligned=RetargetMath.AlignBind(targetBind,Vector3.Transform(Vector3.UnitY,targetBind),direction);
    if (Vector3.Dot(Vector3.Transform(Vector3.UnitY,aligned),direction)<0.99999f)
        throw new Exception("Arm bind alignment did not follow the source segment.");
}
foreach (var bad in new[]{Vector3.Zero,new Vector3(float.NaN,0,0)})
{
    try {RetargetMath.AlignBind(targetBind,bad,Vector3.UnitY); throw new Exception("Invalid arm segment accepted.");}
    catch (IOException) {}
}
Console.WriteLine("Arm calibration passed: bent reference directions and invalid segments.");

string temporary=Path.Combine(Path.GetTempPath(),"skate-cache-check-"+Guid.NewGuid());
try
{
    string cache=Path.Combine(temporary,"Mods","SkateScheduleAssets");
    Directory.CreateDirectory(cache);
    File.WriteAllText(Path.Combine(temporary,"GameAssembly.dll"),"fixture game identity");
    string hash=Convert.ToHexString(System.Security.Cryptography.SHA512.HashData(File.ReadAllBytes(Path.Combine(temporary,"GameAssembly.dll"))));
    using (var writer=new BinaryWriter(File.Create(Path.Combine(cache,"fixture.mesh"))))
    {
        writer.Write(0x314d4b53); writer.Write(3); writer.Write(3);
        foreach (float v in new[]{0f,0,0,1,0,0,0,0,1}) writer.Write(v);
        foreach (int v in new[]{0,1,2}) writer.Write(v);
    }
    void Index(string identity,string filename) => File.WriteAllText(Path.Combine(cache,"index.json"),
        System.Text.Json.JsonSerializer.Serialize(new {version=1,GameAssemblyHash=identity,
            meshes=new[]{new {name="fixture",vertex_count=3,index_counts=new[]{3},bounds=new[]{0f,0,0,1,1,1},file=filename,convex_file=filename}}}));
    Index("wrong identity","fixture.mesh");
    try {await Task.Run(()=>CollisionCache.Initialize(temporary)); throw new Exception("Changed game identity accepted.");}
    catch (IOException) {}
    Index(hash,"../outside.mesh");
    try {await Task.Run(()=>CollisionCache.Initialize(temporary)); throw new Exception("Unsafe cache path accepted.");}
    catch (IOException) {}
    Index(hash,"fixture.mesh");
    await Task.Run(()=>CollisionCache.Initialize(temporary));
    File.Delete(Path.Combine(cache,"fixture.mesh"));
    File.Delete(Path.Combine(temporary,"GameAssembly.dll"));
    var fixture=new UnityEngine.Mesh();
    var geometry=CollisionCache.Get(fixture,false);
    if (geometry.Vertices.Length!=3 || !ReferenceEquals(geometry,CollisionCache.Get(fixture,true)))
        throw new Exception("Cache lookup did not use the preloaded buffer.");
    fixture.name="unknown";
    try {CollisionCache.Get(fixture,false); throw new Exception("Unknown mesh accepted.");}
    catch (IOException) {}
    Console.WriteLine("Cache warmup passed: worker loading, game identity, path validation and lookup without file reads.");
}
finally {Directory.Delete(temporary,true);}
if (args.Length==1)
{
    var watch=System.Diagnostics.Stopwatch.StartNew();
    await Task.Run(()=>CollisionCache.Initialize(args[0]));
    Console.WriteLine($"Installed cache validated and fully preloaded on a worker in {watch.Elapsed.TotalMilliseconds:F1} ms.");
}
