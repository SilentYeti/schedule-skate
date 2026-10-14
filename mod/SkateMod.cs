using HarmonyLib;
using Il2CppScheduleOne.PlayerScripts;
using Il2CppScheduleOne.Skating;
using Il2CppScheduleOne.State;
using Il2CppScheduleOne;
using MelonLoader;
using UnityEngine;

[assembly: MelonInfo(typeof(SkateSchedule.SkateMod),"Skate Schedule","0.1.21","Local prototype")]
[assembly: MelonGame("TVGS","Schedule I")]

namespace SkateSchedule;

public sealed class SkateMod : MelonMod
{
    private bool enabled=true;
    private Skateboard? board;
    private SkateboardCamera? boardCamera;
    private BridgeConnection? connection;
    private Task<Reply>? pending;
    private Task<byte[]>? collisionWork;
    private BridgeConnection? collisionConnection;
    private Task<Reply>? collisionUpload;
    private Task? suspension;
    private bool reusable;
    private IEnumerator<CollisionInstance>? snapshot;
    private List<CollisionInstance>? snapshotParts;
    private double snapshotMilliseconds;
    private Reply? previous;
    private Reply? presentation;
    private float receivedAt;
    private int presentedFrame=-1;
    private Task? cacheWarmup;
    private Reply? frame;
    private Vector3 origin,collisionCentre,boardOffset;
    private Quaternion boardRotationOffset;
    private bool savedKinematic,savedGravity,savedBoardCamera;
    private readonly MountGuard mountGuard=new();
    private float elapsed,savedFov;
    private Vector3 savedCameraPosition;
    private Vector3 savedCameraRootPosition;
    private Quaternion savedCameraRootRotation;
    private Vector3 savedContainerPosition,containerOffset;
    private Quaternion savedContainerRotation;
    private Quaternion savedCameraRotation;
    private Transform? cameraTransform;
    private Transform? cameraRootTransform;
    private bool driving;
    private bool boardHeld;
    private AvatarRetargeter? avatarPose;
    private Vector3 savedVelocity;
    private bool firstReply;
    private bool presentationLogged;
    private float presentationLogAt;
    private string? status;
    private float statusUntil;
    private GUIStyle? statusStyle;
    internal static SkateMod? Current;
    private MelonPreferences_Entry<float>? mouseSensitivity;

    public override void OnInitializeMelon()
    {
        Current=this;
        mouseSensitivity=MelonPreferences.CreateCategory("SkateSchedule").CreateEntry("MouseStickSensitivity",0.08f);
        string gameRoot=MelonLoader.Utils.MelonEnvironment.GameRootDirectory;
        cacheWarmup=Task.Run(()=>
        {
            var watch=System.Diagnostics.Stopwatch.StartNew();
            CollisionCache.Initialize(gameRoot);
            LoggerInstance.Msg($"Collision cache ready: {watch.Elapsed.TotalMilliseconds:F1} ms on a worker.");
        });
        _=cacheWarmup.ContinueWith(t=>LoggerInstance.Error($"Collision cache initialization failed: {t.Exception!.GetBaseException().Message}"),
            TaskContinuationOptions.OnlyOnFaulted);
        foreach (var method in new[]{"GetInput","FixedUpdate","Update","LateUpdate"})
            HarmonyInstance.Patch(AccessTools.Method(typeof(Skateboard),method),
                prefix:new HarmonyMethod(typeof(SkateMod),nameof(AllowNativeBoard)));
        HarmonyInstance.Patch(AccessTools.Method(typeof(PlayerCamera),"LateUpdate"),
            prefix:new HarmonyMethod(typeof(SkateMod),nameof(AllowNativeCamera)),
            postfix:new HarmonyMethod(typeof(SkateMod),nameof(PresentCamera)));
        HarmonyInstance.Patch(AccessTools.Method(typeof(SkateboardCamera),"LateUpdate"),
            prefix:new HarmonyMethod(typeof(SkateMod),nameof(AllowSkateCamera)));
        RuntimeDiagnostics.Start(gameRoot);
        HarmonyInstance.Patch(AccessTools.Method(typeof(Player),"DismountSkateboard"),
            prefix:new HarmonyMethod(typeof(SkateMod),nameof(ObserveDismount)));
        HarmonyInstance.Patch(AccessTools.Method(typeof(Skateboard_Equippable),"Update"),
            prefix:new HarmonyMethod(typeof(SkateMod),nameof(BeginEquipmentUpdate)),
            finalizer:new HarmonyMethod(typeof(SkateMod),nameof(EndEquipmentUpdate)));
        HarmonyInstance.Patch(AccessTools.Method(typeof(MonoState),"RemoveFromDefaultParent"),
            prefix:new HarmonyMethod(typeof(SkateMod),nameof(AllowMountRemoval)));
        LoggerInstance.Msg("Skate 3 mode enabled: mount a skateboard to connect automatically. F8 toggles; F7 selects keyboard/mouse while connected.");
    }
    private static void BeginEquipmentUpdate(Skateboard_Equippable __instance,out IntPtr? __state)
    {
        __state=null;
        var mod=Current;
        if (mod?.boardHeld!=true || mod.board?.Equippable==null || __instance.Pointer!=mod.board.Equippable.Pointer) return;
        __state=mod.mountGuard.Begin(__instance.State.Pointer,GameInput.GetButtonDown(GameInput.ButtonCode.SkateboardDismount));
    }
    private static Exception? EndEquipmentUpdate(Exception? __exception,IntPtr? __state)
    {
        if (__state.HasValue) Current?.mountGuard.End(__state.Value);
        return __exception;
    }
    private static bool AllowMountRemoval(MonoState __instance)
    {
        if (Current==null || Current.mountGuard.AllowRemoval(__instance.Pointer)) return true;
        // Native equipment Update removes its mounted state above 80 degrees; Skate owns flips and bails.
        RuntimeDiagnostics.Record(new {kind="prevented_angle_dismount",state=Current.frame?.state});
        return false;
    }
    private static void ObserveDismount(Player __instance)
    {
        if (Current?.driving==true && Player.Local!=null && __instance.Pointer==Player.Local.Pointer)
            RuntimeDiagnostics.Record(new {kind="native_dismount",time=Time.unscaledTime,state=Current.frame?.state});
    }
    private static bool AllowNativeBoard(Skateboard __instance) =>
        Current==null || !Current.boardHeld || Current.board==null || __instance.Pointer!=Current.board.Pointer;
    private static bool AllowNativeCamera(PlayerCamera __instance) =>
        Current==null || !Current.driving || PlayerCamera.Instance==null || __instance.Pointer!=PlayerCamera.Instance.Pointer;
    private static bool AllowSkateCamera(SkateboardCamera __instance) =>
        Current==null || !Current.driving || Current.board==null || __instance.board==null || __instance.board.Pointer!=Current.board.Pointer;
    private static void PresentCamera(PlayerCamera __instance)
    {
        if (Current?.driving==true && PlayerCamera.Instance!=null && __instance.Pointer==PlayerCamera.Instance.Pointer)
            Current.OnLateUpdate();
    }

    public override void OnUpdate()
    {
        long started=System.Diagnostics.Stopwatch.GetTimestamp();
        if (Input.GetKeyDown(KeyCode.F8))
        {
            enabled=!enabled;
            status=null;
            if (!enabled) Stop();
            LoggerInstance.Msg(enabled?"Skate 3 physics enabled.":"Skate 3 physics disabled.");
        }
        try
        {
            var player=Player.Local;
            var playerCamera=PlayerCamera.Instance;
            if (driving) RuntimeDiagnostics.Record(new {kind="frame_time",time=Time.unscaledTime,dt=Time.unscaledDeltaTime,
                pose_age=Time.unscaledTime-receivedAt,state=frame?.state});
            else if (player?.ActiveSkateboard!=null && Time.timeScale>0 && playerCamera?.ActiveUIElementCount==0)
                RuntimeDiagnostics.Record(new {kind="native_frame_time",time=Time.unscaledTime,dt=Time.unscaledDeltaTime,preparing=board!=null});
            if (!enabled || player==null || player.ActiveSkateboard==null || Player.PlayerList.Count>1 ||
                Time.timeScale==0 || playerCamera==null || playerCamera.ActiveUIElementCount>0)
            {
                Stop(); return;
            }
            if (board!=null && board.Pointer!=player.ActiveSkateboard.Pointer) Stop();
            if (driving)
            {
                if (Input.GetKeyDown(KeyCode.F7))
                {
                    Controller.PreferKeyboard=!Controller.PreferKeyboard;
                    Controller.Reset();
                    LoggerInstance.Msg(Controller.PreferKeyboard?"Keyboard/mouse selected.":"Controller preferred; keyboard fallback.");
                }
                Controller.Poll(mouseSensitivity!.Value);
            }
            if (suspension!=null)
            {
                if (!suspension.IsCompleted) return;
                suspension.GetAwaiter().GetResult(); suspension=null;
            }
            if (board==null)
            {
                if (cacheWarmup?.IsCompleted!=true) return;
                cacheWarmup.GetAwaiter().GetResult();
                Start(player.ActiveSkateboard); return;
            }
            AdvanceSnapshot();
            if (collisionUpload?.IsCompleted==true) {collisionUpload.GetAwaiter().GetResult(); collisionUpload=null;}
            if (driving && frame!=null) elapsed=Math.Min(elapsed+Time.deltaTime,0.15f);
            if (pending!=null)
            {
                if (!pending.IsCompleted) return;
                var next=pending.GetAwaiter().GetResult(); pending=null;
                if (driving && frame?.state!=next.state)
                {
                    RuntimeDiagnostics.Record(new {kind="state_change",from=frame?.state,to=next.state,next.tick});
                }
                previous=driving?frame:null; frame=next; receivedAt=Time.unscaledTime;
                if (firstReply)
                {
                    if (!boardHeld) {HoldAndResume(); return;}
                    var pose=frame.BoardPose();
                    boardRotationOffset=Quaternion.Inverse(pose.rotation)*board.transform.rotation;
                    boardOffset=Quaternion.Inverse(pose.rotation)*(board.transform.position-origin-(Vector3)pose.GetColumn(3));
                    LoggerInstance.Msg($"Board model offset: {boardOffset}; camera uses the same translation.");
                    Capture();
                    firstReply=false; elapsed=0; reusable=true;
                    LoggerInstance.Msg($"Skating simulation connected: {frame.bones.Length} bones.");
                    status=null;
                }
            }
            if (!driving || connection==null || frame==null) return;
            if (collisionWork?.IsCompleted==true)
            {
                var packed=collisionWork.GetAwaiter().GetResult(); collisionWork=null;
                var transport=collisionConnection!;
                collisionUpload=Task.Run(()=>transport.SendPacked(new {op="collision_packed",count=packed.Length/36},packed,capture:true));
            }
            var focus=CollisionFocus.Position(frame.state,CollisionExport.Numeric(board.transform.position),
                CollisionExport.Numeric(origin+Space.FromEngine(frame.root[12..15])));
            var focusPosition=new Vector3(focus.X,focus.Y,focus.Z);
            if (snapshot==null && collisionWork==null && collisionUpload==null && !frame.collision_pending &&
                Vector3.Distance(focusPosition,collisionCentre)>CollisionExport.Radius*0.4f)
            {
                collisionCentre=focusPosition;
                BeginSnapshot();
            }
            int ticks=Math.Min(8,(int)(elapsed/frame.period));
            if (ticks<1) return;
            elapsed-=ticks*frame.period;
            var input=Controller.Step((byte)ticks,playerCamera.Camera.aspect);
            var client=connection;
            pending=Task.Run(()=>client.Send(input));
        }
        catch (Exception error)
        {
            RuntimeDiagnostics.Record(new {kind="bridge_error",message=error.Message,state=frame?.state,
                centre=new[]{collisionCentre.x,collisionCentre.y,collisionCentre.z}});
            enabled=false; Stop(true);
            NotifyError("Skate 3 stopped: "+error.Message);
            LoggerInstance.Error($"Skating stopped: {error.Message}");
        }
        finally
        {
            if (board!=null && Time.frameCount%4==0)
                RuntimeDiagnostics.Record(new {kind="update_time",ms=(System.Diagnostics.Stopwatch.GetTimestamp()-started)*1000.0/System.Diagnostics.Stopwatch.Frequency});
        }
    }
    private void Start(Skateboard next)
    {
        if (!Physics.Raycast(next.transform.position+Vector3.up,Vector3.down,out var hit,5,
            next.GroundDetectionMask.value,QueryTriggerInteraction.Ignore))
            throw new InvalidOperationException("Mount the board on solid ground before enabling the bridge.");
        board=next; firstReply=true;
        connection??=new BridgeConnection(); collisionConnection??=new BridgeConnection();
        if (reusable && Vector3.Distance(next.transform.position,collisionCentre)<CollisionExport.Radius*0.4f)
        {
            HoldAndResume(); return;
        }
        reusable=false; origin=hit.point; collisionCentre=next.transform.position;
        // Native riding continues while bounded snapshot batches and the warmed host prepare the world.
        BeginSnapshot();
    }
    private void HoldAndResume()
    {
        var next=board!;
        if (!Physics.Raycast(next.transform.position+Vector3.up,Vector3.down,out var hit,5,
            next.GroundDetectionMask.value,QueryTriggerInteraction.Ignore))
            throw new InvalidOperationException("The skateboard left solid ground during preparation.");
        float heading=CameraMath.Heading(CollisionExport.Numeric(next.FrontAxlePosition.position),CollisionExport.Numeric(next.RearAxlePosition.position));
        float aspect=PlayerCamera.Instance.Camera.aspect;
        var spawn=Space.ToEngine(hit.point-origin);
        savedKinematic=next.Rb.isKinematic; savedGravity=next.Rb.useGravity; savedVelocity=next.Rb.velocity;
        var velocity=Space.ToEngine(savedVelocity);
        boardHeld=true; next.Rb.isKinematic=true; next.Rb.useGravity=false;
        var client=connection!;
        pending=Task.Run(()=>client.Send(new {op="resume",spawn,heading,aspect,velocity}));
    }
    private void BeginSnapshot()
    {
        RuntimeDiagnostics.Record(new {kind="snapshot_start",state=frame?.state,mask=board!.GroundDetectionMask.value,
            centre=new[]{collisionCentre.x,collisionCentre.y,collisionCentre.z}});
        snapshot=CollisionExport.Snapshot(collisionCentre,board!.GroundDetectionMask.value,Player.Local.transform,board.transform).GetEnumerator();
        snapshotParts=new List<CollisionInstance>(); snapshotMilliseconds=0;
    }
    private void AdvanceSnapshot()
    {
        if (snapshot==null) return;
        var watch=System.Diagnostics.Stopwatch.StartNew();
        while (watch.Elapsed.TotalMilliseconds<2)
        {
            if (snapshot.MoveNext()) snapshotParts!.Add(snapshot.Current);
            else
            {
                snapshot.Dispose(); snapshot=null;
                var instances=snapshotParts!.ToArray(); snapshotParts=null;
                LoggerInstance.Msg($"Collision snapshot: {instances.Length} colliders, {snapshotMilliseconds+watch.Elapsed.TotalMilliseconds:F1} ms total across frame batches.");
                var centre=CollisionExport.Numeric(collisionCentre); var offset=CollisionExport.Numeric(origin);
                if (!driving)
                {
                    float heading=CameraMath.Heading(CollisionExport.Numeric(board!.FrontAxlePosition.position),CollisionExport.Numeric(board.RearAxlePosition.position));
                    float aspect=PlayerCamera.Instance.Camera.aspect;
                    var client=connection!;
                    pending=Task.Run(()=>
                    {
                        var packed=CollisionCrop.GatherPacked(instances,centre,offset);
                        return client.SendPacked(new {op="activate_packed",count=packed.Length/36,spawn=new[]{0f,0f,0f},heading,aspect},packed,capture:true);
                    });
                }
                else collisionWork=Task.Run(()=>CollisionCrop.GatherPacked(instances,centre,offset));
                break;
            }
        }
        snapshotMilliseconds+=watch.Elapsed.TotalMilliseconds;
        RuntimeDiagnostics.Record(new {kind="snapshot_batch",ms=watch.Elapsed.TotalMilliseconds});
    }
    private void Capture()
    {
        boardCamera=board!.GetComponent<SkateboardCamera>();
        savedBoardCamera=boardCamera!=null && boardCamera.enabled;
        cameraTransform=PlayerCamera.Instance.Camera.transform;
        savedCameraPosition=cameraTransform.localPosition; savedCameraRotation=cameraTransform.localRotation;
        cameraRootTransform=PlayerCamera.Instance.transform;
        savedCameraRootPosition=cameraRootTransform.localPosition; savedCameraRootRotation=cameraRootTransform.localRotation;
        savedFov=PlayerCamera.Instance.Camera.fieldOfView;
        driving=true;
        savedContainerPosition=board.PlayerContainer.localPosition; savedContainerRotation=board.PlayerContainer.localRotation;
        var root=Space.Matrix(frame!.root);
        containerOffset=Quaternion.Inverse(root.rotation)*(board.PlayerContainer.position-origin-(Vector3)root.GetColumn(3));
        avatarPose=new AvatarRetargeter(Player.Local.Avatar,frame!);
        avatarPose.Hold();
        LoggerInstance.Msg($"Avatar retargeting: {avatarPose.BoneCount} bones; leg scale={avatarPose.Scale:F3}.");
        presentationLogged=false; presentationLogAt=Time.unscaledTime+1;
        if (boardCamera!=null) boardCamera.enabled=false;
    }
    public override void OnLateUpdate()
    {
        if (!driving || board==null || frame==null || presentedFrame==Time.frameCount) return;
        presentedFrame=Time.frameCount;
        long started=System.Diagnostics.Stopwatch.GetTimestamp();
        try
        {
            var visible=frame;
            if (previous!=null && previous.tick<frame.tick && Vector3.Distance(Space.FromEngine(previous.root[12..15]),Space.FromEngine(frame.root[12..15]))<3)
            {
                presentation??=new Reply();
                float duration=(frame.tick-previous.tick)*frame.period;
                presentation.Interpolate(previous,frame,Math.Clamp((Time.unscaledTime-receivedAt)/duration,0,1));
                visible=presentation;
            }
            long blended=System.Diagnostics.Stopwatch.GetTimestamp();
            var pose=visible.BoardPose();
            board.transform.SetPositionAndRotation(origin+(Vector3)pose.GetColumn(3)+pose.rotation*boardOffset,
                pose.rotation*boardRotationOffset);
            // The native board late update no longer keeps its rider container upright during deck flips.
            var root=Space.Matrix(visible.root);
            var upright=Quaternion.Euler(0,root.rotation.eulerAngles.y,0);
            board.PlayerContainer.SetPositionAndRotation(origin+(Vector3)root.GetColumn(3)+upright*containerOffset,upright);
            board.CurrentSpeed_Kmh=Space.FromEngine(frame.velocity).magnitude*3.6f;
            long positioned=System.Diagnostics.Stopwatch.GetTimestamp();
            avatarPose?.Apply(visible,board.transform,origin);
            long animated=System.Diagnostics.Stopwatch.GetTimestamp();
            if (Time.frameCount%4==0)
            {
                bool ground=Physics.Raycast(board.transform.position+Vector3.up*2,Vector3.down,out var hit,10,
                    board.GroundDetectionMask.value,QueryTriggerInteraction.Ignore);
                var riderPosition=origin+(Vector3)root.GetColumn(3);
                RaycastHit riderHit=default;
                bool riderGround=CollisionFocus.Detached(visible.state) &&
                    Physics.Raycast(riderPosition+Vector3.up*2,Vector3.down,out riderHit,10,
                        board.GroundDetectionMask.value,QueryTriggerInteraction.Ignore);
                var focus=CollisionFocus.Position(visible.state,CollisionExport.Numeric(board.transform.position),CollisionExport.Numeric(riderPosition));
                RuntimeDiagnostics.Record(new {kind="frame",time=Time.unscaledTime,dt=Time.unscaledDeltaTime,
                    pose_age=Time.unscaledTime-receivedAt,state=visible.state,
                    board=new[]{board.transform.position.x,board.transform.position.y,board.transform.position.z},
                    player=new[]{Player.Local.transform.position.x,Player.Local.transform.position.y,Player.Local.transform.position.z},
                    container=new[]{board.PlayerContainer.position.x,board.PlayerContainer.position.y,board.PlayerContainer.position.z},
                    rider=new[]{riderPosition.x,riderPosition.y,riderPosition.z},
                    rider_ground_y=riderGround?(float?)riderHit.point.y:null,
                    collision_focus_distance=System.Numerics.Vector3.Distance(focus,CollisionExport.Numeric(collisionCentre)),
                    ground_y=ground?(float?)hit.point.y:null});
            }
            long queried=System.Diagnostics.Stopwatch.GetTimestamp();
            var camera=visible.camera;
            if (camera!=null && PlayerCamera.Instance!=null)
            {
                var (at,above)=CameraMath.Directions(camera.basis);
                var forward=new Vector3(at.X,at.Y,at.Z);
                var up=new Vector3(above.X,above.Y,above.Z);
                var target=PlayerCamera.Instance.Camera;
                var offset=pose.rotation*boardOffset;
                var position=CameraMath.Position(camera.position,CollisionExport.Numeric(origin),CollisionExport.Numeric(offset));
                var worldPosition=new Vector3(position.X,position.Y,position.Z);
                var worldRotation=Quaternion.LookRotation(forward,up);
                // Avatar culling reads the camera container rather than the render camera child.
                cameraRootTransform!.SetPositionAndRotation(worldPosition,worldRotation);
                target.transform.SetPositionAndRotation(worldPosition,worldRotation);
                target.fieldOfView=Math.Clamp(camera.fov,20,120);
                if (!presentationLogged && Time.unscaledTime>=presentationLogAt)
                {
                    presentationLogged=true;
                    var avatar=Player.Local.Avatar;
                    LoggerInstance.Msg($"Camera alignment: aspect={target.aspect:F3}, FOV={target.fieldOfView:F2}, "+
                        $"board viewport={target.WorldToViewportPoint(board.transform.position)}, "+
                        $"avatar viewport={(avatar!=null?target.WorldToViewportPoint(avatar.CenterPoint):Vector3.zero)}, "+
                        $"board world={board.transform.position}, camera world={target.transform.position}.");
                }
            }
            if (Time.frameCount%4==0)
            {
                double scale=1000.0/System.Diagnostics.Stopwatch.Frequency;
                RuntimeDiagnostics.Record(new {kind="presentation_time",blend_ms=(blended-started)*scale,
                    board_ms=(positioned-blended)*scale,avatar_ms=(animated-positioned)*scale,
                    ground_ms=(queried-animated)*scale,camera_ms=(System.Diagnostics.Stopwatch.GetTimestamp()-queried)*scale});
            }
        }
        catch (Exception error)
        {
            enabled=false; Stop(true); LoggerInstance.Error($"Skating presentation stopped: {error.Message}");
            NotifyError("Skate 3 stopped: "+error.Message);
        }
    }
    private void Stop(bool close=false)
    {
        Controller.Reset();
        snapshot?.Dispose(); snapshot=null; snapshotParts=null;
        if (close)
        {
            if (suspension!=null) _=suspension.ContinueWith(t=>{_=t.Exception;},TaskContinuationOptions.OnlyOnFaulted);
            suspension=null;
            connection?.Dispose(); connection=null;
            collisionConnection?.Dispose(); collisionConnection=null;
            reusable=false;
        }
        else if (board!=null && connection!=null)
        {
            var client=connection; var wait=pending; var upload=collisionUpload;
            suspension=Task.Run(async ()=>
            {
                if (wait!=null) await wait;
                if (upload!=null) await upload;
                client.Send(new {op="suspend"},requireFrame:false);
            });
        }
        if (pending!=null) _=pending.ContinueWith(t=>{_ = t.Exception;},TaskContinuationOptions.OnlyOnFaulted);
        if (collisionUpload!=null) _=collisionUpload.ContinueWith(t=>{_=t.Exception;},TaskContinuationOptions.OnlyOnFaulted);
        pending=null; collisionUpload=null;
        if (collisionWork!=null) _=collisionWork.ContinueWith(t=>{_=t.Exception;},TaskContinuationOptions.OnlyOnFaulted);
        collisionWork=null;
        void Restore(Action action)
        {
            try {action();}
            catch (Exception error) {LoggerInstance.Warning($"Could not restore a removed scene object: {error.Message}");}
        }
        if (boardHeld && board!=null) Restore(()=>
        {
            if (board.Rb==null) return;
            board.Rb.isKinematic=savedKinematic; board.Rb.useGravity=savedGravity;
            if (!savedKinematic) board.Rb.velocity=driving && frame!=null?Space.FromEngine(frame.velocity):savedVelocity;
        });
        if (driving)
        {
            if (board?.PlayerContainer!=null) Restore(()=>
            {
                board.PlayerContainer.localPosition=savedContainerPosition; board.PlayerContainer.localRotation=savedContainerRotation;
            });
            if (avatarPose!=null) Restore(()=>avatarPose.Dispose());
            if (boardCamera!=null) Restore(()=>boardCamera.enabled=savedBoardCamera);
            if (PlayerCamera.Instance!=null) Restore(()=>
            {
                PlayerCamera.Instance.Camera.fieldOfView=savedFov;
            });
            if (cameraRootTransform!=null) Restore(()=>
            {
                cameraRootTransform.localPosition=savedCameraRootPosition; cameraRootTransform.localRotation=savedCameraRootRotation;
            });
            if (cameraTransform!=null) Restore(()=>
            {
                cameraTransform.localPosition=savedCameraPosition; cameraTransform.localRotation=savedCameraRotation;
            });
        }
        board=null; boardCamera=null; cameraTransform=null; cameraRootTransform=null; avatarPose=null; frame=null; previous=null; presentation=null;
        driving=false; boardHeld=false; elapsed=0; presentedFrame=-1;
    }
    private void NotifyError(string message) {status=message; statusUntil=Time.unscaledTime+10;}
    public override void OnGUI()
    {
        if (status==null || Time.unscaledTime>statusUntil) return;
        statusStyle??=new GUIStyle(GUI.skin.box) {fontSize=18,wordWrap=true,alignment=TextAnchor.MiddleLeft};
        GUI.Box(new Rect(20,20,Math.Min(Screen.width-40,900),72),status,statusStyle);
    }
    public override void OnSceneWasUnloaded(int buildIndex,string sceneName) {Stop(true); CollisionExport.Clear();}
    public override void OnDeinitializeMelon() {Stop(true); RuntimeDiagnostics.Stop(); Current=null;}
}
