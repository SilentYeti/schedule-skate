namespace SkateSchedule;

static class FailureCapture
{
    public static void Save(string directory,string request,string response)
    {
        string destination=Path.Combine(directory,"latest-failure");
        Directory.CreateDirectory(destination);
        foreach (string old in Directory.EnumerateFiles(destination,"*.skb")) File.Delete(old);
        // Keep nearby worlds before continued play evicts them; native contact history still needs a replay.
        foreach (string file in Directory.EnumerateFiles(directory,"*.skb").OrderByDescending(File.GetLastWriteTimeUtc).Take(8))
            File.Copy(file,Path.Combine(destination,Path.GetFileName(file)),true);
        string trace=Path.Combine(directory,"runtime.jsonl");
        if (File.Exists(trace)) File.Copy(trace,Path.Combine(destination,"runtime.jsonl"),true);
        File.WriteAllText(Path.Combine(destination,"rejected-request.json"),request);
        File.WriteAllText(Path.Combine(destination,"rejected-response.json"),response);
    }
}
