using System.Collections.Concurrent;
using Newtonsoft.Json;

namespace SkateSchedule;

static class RuntimeDiagnostics
{
    private static readonly ConcurrentQueue<(double At,object Data)> records=new();
    private static CancellationTokenSource? cancellation;
    private static Task? writer;
    public static void Start(string root)
    {
        string directory=Path.Combine(root,"SkateScheduleDiagnostics"); Directory.CreateDirectory(directory);
        cancellation=new CancellationTokenSource(); var token=cancellation.Token;
        writer=Task.Run(async ()=>
        {
            try
            {
                using var output=new StreamWriter(Path.Combine(directory,"runtime.jsonl"),false);
                int remaining=40000;
                while (!token.IsCancellationRequested || !records.IsEmpty)
                {
                    while (records.TryDequeue(out var record))
                        if (remaining>0) {remaining--; await output.WriteLineAsync(JsonConvert.SerializeObject(new {at_ms=record.At,record=record.Data}));}
                    await output.FlushAsync();
                    if (!token.IsCancellationRequested) await Task.Delay(100);
                }
            }
            catch (Exception error) {MelonLoader.MelonLogger.Warning($"Skate diagnostics writer stopped: {error.Message}");}
        });
    }
    public static void Record(object record)
    {
        if (cancellation!=null && !cancellation.IsCancellationRequested && records.Count<1000)
            records.Enqueue((System.Diagnostics.Stopwatch.GetTimestamp()*1000.0/System.Diagnostics.Stopwatch.Frequency,record));
    }
    public static void Stop() {cancellation?.Cancel(); writer?.Wait(500);}
}
