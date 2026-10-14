"""Check Steam command/environment forwarding and bridge cleanup without launching games."""
from pathlib import Path
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import launch


class LaunchChecks(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.game=self.root/'library with spaces'/'Schedule I'
        (self.game/'Mods').mkdir(parents=True)
        (self.game/'Mods/SkateSchedule.dll').write_bytes(b'test adapter')
        executable=self.root/'target/debug/skate-schedule'
        executable.parent.mkdir(parents=True); executable.touch()
        runtime=self.root/'steamapps/common/SteamLinuxRuntime_sniper/run'
        runtime.parent.mkdir(parents=True); runtime.touch()
        self.bridge=MagicMock(); self.bridge.poll.return_value=None
        self.environment={'STEAM_COMPAT_DATA_PATH':'/selected prefix', 'SteamAppId':'3164500',
                          'STEAM_COMPAT_CLIENT_INSTALL_PATH':'/selected steam',
                          'WINEDLLOVERRIDES':'other=n,b','SteamGameId':'3164500'}
        stack=contextlib.ExitStack(); self.addCleanup(stack.close)
        for attribute,value in (('PROJECT',self.root),('STEAM',self.root)):
            stack.enter_context(patch.object(launch,attribute,value))
        stack.enter_context(patch.object(launch,'require_game_closed'))
        stack.enter_context(patch.object(launch.time,'sleep'))
        probe=stack.enter_context(patch.object(launch.socket,'socket'))
        probe.return_value.__enter__.return_value.connect_ex.return_value=1
        self.spawn=stack.enter_context(patch.object(launch.subprocess,'Popen',return_value=self.bridge))
        self.run=stack.enter_context(patch.object(launch.subprocess,'run'))
        stack.enter_context(patch.dict(os.environ,self.environment,clear=True))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def invoke(self,*args):
        with patch.object(sys,'argv',['launch.py',*args]): launch.main()

    def test_steam_preserves_runtime_prefix_and_argument_boundaries(self):
        command=['/chosen runtime/run','--','/chosen proton/proton','waitforexitandrun',
                 str(self.game/'Schedule I.exe'),'--existing-option=two words']
        self.invoke('--steam-command',*command)
        args,kwargs=self.run.call_args
        self.assertEqual(args[0],command+launch.LOADER_FLAGS)
        self.assertEqual(kwargs['cwd'],self.game)
        self.assertEqual(kwargs['env'],{**self.environment,'WINEDLLOVERRIDES':'other=n,b;version=n,b'})
        self.bridge.terminate.assert_called_once()
        self.bridge.wait.assert_called_once_with(timeout=10)

    def test_standalone_still_uses_proton_and_runtime(self):
        self.invoke('--game',str(self.game),'--proton','/custom/proton','--game-option')
        command=self.run.call_args.args[0]
        self.assertEqual(command,[str(self.root/'steamapps/common/SteamLinuxRuntime_sniper/run'),'--',
                                  '/custom/proton','run',str(self.game/'Schedule I.exe'),
                                  *launch.LOADER_FLAGS,'--game-option'])

    def test_game_launch_failure_stops_owned_bridge(self):
        self.run.side_effect=subprocess.CalledProcessError(7,['game'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke('--game',str(self.game),'--steam-command','/fake/steam-command')
        self.bridge.terminate.assert_called_once()

    def test_empty_steam_command_is_rejected_before_starting_bridge(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            self.invoke('--steam-command')
        self.assertEqual(error.exception.code,2)
        self.spawn.assert_not_called()

    def test_missing_bridge_is_rejected_without_truncating_logs(self):
        (self.root/'target/debug/skate-schedule').unlink()
        log=self.root/'data/logs/bridge.log'
        log.parent.mkdir(parents=True); log.write_text('previous diagnostics')
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            self.invoke('--game',str(self.game),'--steam-command','/fake/steam-command')
        self.assertEqual(error.exception.code,2)
        self.assertEqual(log.read_text(),'previous diagnostics')
        self.spawn.assert_not_called()


if __name__=='__main__': unittest.main()
