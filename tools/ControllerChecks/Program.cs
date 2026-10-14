using System.Runtime.InteropServices;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SkateSchedule;
using UnityEngine;

// Exercise the actual controller code and serializer without a running Unity game.
NativeLibrary.SetDllImportResolver(typeof(Controller).Assembly, (name, _, _) =>
    name == "xinput1_4.dll" ? NativeLibrary.Load(args[0]) : IntPtr.Zero);
void Check(bool condition,string message) {if (!condition) throw new Exception(message);}
JObject Request() => JObject.FromObject(Controller.Step(1));
void Poll(params KeyCode[] keys) {Input.Keys=new(keys);Controller.Poll();}
Poll(KeyCode.Space,KeyCode.W,KeyCode.LeftArrow);
Console.WriteLine(JsonConvert.SerializeObject(Controller.Step(8))); // Keyboard fallback.

Controller.Reset();
Poll(KeyCode.Space);Poll();
Check((int)Request()["buttons"]! == 0x1000,"A short push press vanished between bridge requests");
Check((int)Request()["buttons"]! == 0,"A released push stayed held");
Poll(KeyCode.C,KeyCode.E,KeyCode.F,KeyCode.Q,KeyCode.R,KeyCode.LeftShift,KeyCode.Tab,KeyCode.Return,
     KeyCode.I,KeyCode.J,KeyCode.K,KeyCode.L);
Input.MouseButtons=new(){0,1,2};Controller.Poll();
var all=Request();
Check((int)all["buttons"]! == 0xe3ff,"Face buttons, bumpers, stick clicks or D-pad missing");
Check(all["triggers"]!.ToString(Formatting.None)=="[255,255]","Mouse grabs were not analog trigger bytes");
Input.MouseButtons.Clear();Poll(KeyCode.Z,KeyCode.X);
Check(Request()["triggers"]!.ToString(Formatting.None)=="[255,255]","Keyboard grab alternatives failed");
Poll(KeyCode.UpArrow,KeyCode.LeftControl);
Check((int)Request()["right"]![1]! == 16383,"Half-stick keyboard manual input failed");

Controller.Reset();Input.MouseX=99;Input.MouseY=99;Poll(KeyCode.LeftAlt);
Check(Request()["right"]!.ToString(Formatting.None)=="[0,0]","Old camera movement entered a new mouse gesture");
Input.MouseX=0;Input.MouseY=-12.5f;Controller.Poll();
var charge=Controller.Step(1);
Input.MouseX=Input.MouseY=0;Controller.Poll();
Check((int)Request()["right"]![1]! == -32767,"Stationary mouse lost its charged stick position");
Input.MouseX=11.35765f;Input.MouseY=17.71482f;Controller.Poll();
var flick=Controller.Step(1);
Check((int)JObject.FromObject(flick)["right"]![0]! > 29000 && (int)JObject.FromObject(flick)["right"]![1]! > 13000,
      "Mouse reversal did not produce a diagonal flick");
Input.MouseX=Input.MouseY=0;Poll();
Check(Request()["right"]!.ToString(Formatting.None)=="[0,0]","Alt release left a stuck trick stick");
Poll(KeyCode.LeftAlt);Input.MouseY=100;Controller.Poll();Controller.Reset();
Check(Request()["right"]!.ToString(Formatting.None)=="[0,0]","Pause/reset retained a trick gesture");
Input.MouseX=Input.MouseY=0;Poll(KeyCode.LeftAlt);Input.MouseY=100;Controller.Poll(float.NaN);
Check((int)Request()["right"]![1]! == 32767,"Invalid sensitivity reached the protocol");
Input.MouseY=0;Controller.Reset();Poll(KeyCode.Space,KeyCode.W,KeyCode.LeftArrow);
PadFixture.Enable();
Console.WriteLine(JsonConvert.SerializeObject(Controller.Step(1,1.25f))); // Connected controller and a resized view.
Controller.PreferKeyboard=true;
var forced=Request();
Check((int)forced["right"]![0]! == -32767 && (int)forced["triggers"]![1]! == 0,"Idle connected pad overrode selected keyboard input");
Console.WriteLine(JsonConvert.SerializeObject(charge));
Console.WriteLine(JsonConvert.SerializeObject(flick));

static class PadFixture
{
    [DllImport("xinput1_4.dll", EntryPoint="EnablePad")]
    internal static extern void Enable();
}

namespace UnityEngine
{
    enum KeyCode { Space, D, A, W, S, RightArrow, LeftArrow, UpArrow, DownArrow,LeftAlt,RightAlt,
        LeftControl,RightControl,C,E,F,Q,R,LeftShift,RightShift,Tab,Return,I,J,K,L,Z,X }
    static class Input
    {
        internal static HashSet<KeyCode> Keys=new();
        internal static HashSet<int> MouseButtons=new();
        internal static float MouseX,MouseY;
        public static bool GetKey(KeyCode key) => Keys.Contains(key);
        public static bool GetMouseButton(int button) => MouseButtons.Contains(button);
        public static float GetAxisRaw(string axis) => axis=="Mouse X"?MouseX:MouseY;
    }
}
