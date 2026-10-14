using AsmResolver.DotNet;
using AsmResolver.DotNet.Serialized;
using Il2CppInterop.Common;
using Il2CppInterop.Generator;
using Il2CppInterop.Generator.Runners;
using Microsoft.Extensions.Logging;

if (args.Length >= 2 && (args[0] == "inspect" || args[0] == "types"))
{
    using var assembly = Mono.Cecil.AssemblyDefinition.ReadAssembly(args[1]);
    foreach (var type in assembly.MainModule.GetTypes().Where(t => args.Skip(2).Any(n => args[0]=="types"?t.FullName.Contains(n):t.FullName==n)))
    {
        Console.WriteLine($"TYPE {type.FullName} : {type.BaseType}");
        if (args[0]=="types") continue;
        foreach (var field in type.Fields)
            Console.WriteLine($"  FIELD {field.Attributes} {field.FieldType} {field.Name}{(field.HasConstant?$" = {field.Constant}":"")}");
        foreach (var property in type.Properties)
            Console.WriteLine($"  PROPERTY {property.PropertyType} {property.Name}");
        foreach (var method in type.Methods.Where(m => !m.Name.Contains("Rpc") && !m.Name.Contains("Network")))
        {
            Console.WriteLine($"  METHOD {method.Attributes} {method.ReturnType} {method.Name}({string.Join(", ",method.Parameters.Select(p=>$"{p.ParameterType} {p.Name}"))})");
            foreach (var attribute in method.CustomAttributes.Where(a=>a.AttributeType.Name=="AddressAttribute"))
                Console.WriteLine($"    ADDRESS {string.Join(", ",attribute.Fields.Select(f=>$"{f.Name}={f.Argument.Value}"))}");
        }
    }
}
else if (args.Length == 5 && args[0] == "generate")
{
    using var resolver = new InteropResolver();
    var modules = Directory.GetFiles(args[1], "*.dll")
        .Select(path => ModuleDefinition.FromFile(path, new ModuleReaderParameters { ModuleResolver = resolver }))
        .ToList();
    foreach (var module in modules) resolver.Add(module);
    var options = new GeneratorOptions {
        Source = modules.Select(m=>m.Assembly!).ToList(),
        UnityBaseLibsDir = args[2], OutputDir = args[3], GameAssemblyPath = args[4],
        Parallel = true, Il2CppPrefixMode = GeneratorOptions.PrefixMode.OptOut,
    };
    Il2CppInteropGenerator.Create(options).AddLogger(new ConsoleLogger()).AddInteropAssemblyGenerator().Run();
    using var game = File.OpenRead(args[4]);
    var hash = Convert.ToHexString(System.Security.Cryptography.SHA512.HashData(game));
    File.WriteAllText(Path.Combine(args[3],"generation.json"),
        System.Text.Json.JsonSerializer.Serialize(new {GameAssemblyHash=hash, UnityVersion="2022.3.62"}));
}
else
{
    Console.Error.WriteLine("types <assembly> <name fragments...> | inspect <assembly> <type names...> | generate <dump dir> <unity libs> <output> <GameAssembly.dll>");
    return 1;
}
return 0;

// The resolver and generator setup follow MelonLoader's Apache-2.0 assembly generation pipeline.
sealed class InteropResolver : INetModuleResolver, IDisposable
{
    private readonly Dictionary<string,ModuleDefinition> cache=new();
    public void Add(ModuleDefinition module) => cache[module.Name!.ToString()]=module;
    public ModuleDefinition? Resolve(string name) => cache.GetValueOrDefault(name);
    public void Dispose() => cache.Clear();
}
sealed class ConsoleLogger : ILogger
{
    public IDisposable BeginScope<TState>(TState state) where TState : notnull => null!;
    public bool IsEnabled(LogLevel level) => level>=LogLevel.Information;
    public void Log<TState>(LogLevel level, EventId id, TState state, Exception? exception, Func<TState,Exception?,string> formatter) {
        if (IsEnabled(level)) Console.WriteLine(formatter(state,exception));
    }
}
