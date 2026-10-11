"""No live service controls, credentials or SMTP: all probes use disposable fixtures."""
import ast
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import sys
if sys.platform == 'win32':
    raise unittest.SkipTest('The email watchdog uses Linux systemd and fcntl')

ROOT=pathlib.Path(__file__).resolve().parents[1]/'douyin-bot-panel/app'
spec=importlib.util.spec_from_file_location('tested_alerts',ROOT/'fault_alerts.py')
alerts=importlib.util.module_from_spec(spec);spec.loader.exec_module(alerts)

class PlannedStopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control=pathlib.Path(self.temp.name)/'control.json'
        self.patcher=patch.object(alerts,'CONTROL',self.control);self.patcher.start();self.addCleanup(self.patcher.stop)
        self.cfg={**alerts.DEFAULT,'enabled':True}
        self.sent=[]
    def test_systemctl_clean_stop_is_quiet(self):
        with patch.object(alerts,'command',return_value='ActiveState=inactive\nResult=success\nInvocationID=\nNRestarts=0') as command,patch.object(alerts.httpx,'Client',side_effect=AssertionError('Must not probe dependencies while stopped')):
            state={'incidents':{'proxy':{'since':1,'last_sent':2}},'faults':{'service':True,'proxy':True,'monitor':True}}
            self.assertEqual(alerts.collect(state),{})
            alerts.transition(state,{'service':True,'proxy':True},self.cfg,99999,lambda *args:self.sent.append(args))
            self.assertEqual(command.call_count,1)
            self.assertTrue(state['monitoring_paused']);self.assertEqual(state['incidents'],{});self.assertEqual(state['faults'],{});self.assertEqual(self.sent,[])
    def test_failed_exit_and_restarts_are_not_suppressed(self):
        self.assertFalse(alerts.planned_stop({'ActiveState':'failed','Result':'exit-code'},{}))
        self.assertFalse(alerts.planned_stop({'ActiveState':'activating','Result':'exit-code'},{}))
        state={}
        for now in (10000,10180):alerts.transition(state,{'service':True},self.cfg,now,lambda *args:self.sent.append(args))
        self.assertEqual(len(self.sent),1)
    def test_panel_stop_bound_to_invocation(self):
        control={'desired_running':False,'stopped_invocation':'old'}
        for phase in ('active','deactivating','inactive','failed'):
            self.assertTrue(alerts.planned_stop({'ActiveState':phase,'Result':'exit-code','InvocationID':'old'},control))
        for phase in ('active','failed'):
            self.assertFalse(alerts.planned_stop({'ActiveState':phase,'Result':'exit-code','InvocationID':'new'},control))
        self.assertFalse(alerts.planned_stop({'ActiveState':'failed','Result':'exit-code','InvocationID':'old'},{'desired_running':True}))
    def test_intent_atomic_and_private(self):
        with patch.object(alerts,'command',return_value='old\n'):alerts.record_run_intent(False)
        self.assertEqual(alerts.read(self.control,{})['stopped_invocation'],'old')
        self.assertEqual(self.control.stat().st_mode&0o777,0o600)
        alerts.record_run_intent(True);self.assertTrue(alerts.read(self.control,{})['desired_running'])
    def test_resume_starts_fresh_fault_delay(self):
        state={'monitoring_paused':True,'incidents':{'login':{'since':1,'last_sent':2}}}
        alerts.transition(state,{'login':True},self.cfg,10000,lambda *args:self.sent.append(args))
        state['monitoring_paused']=False
        alerts.transition(state,{'login':True},self.cfg,10100,lambda *args:self.sent.append(args))
        self.assertEqual(self.sent,[])
        alerts.transition(state,{'login':True},self.cfg,10280,lambda *args:self.sent.append(args));self.assertEqual(len(self.sent),1)
    def test_panel_actions_and_failed_stop_restore(self):
        tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_bot_action')
        namespace={'fault_alerts':alerts,'BOT_UNIT':'bot','XVFB_UNIT':'xvfb','unit_properties':lambda *args:{'ActiveState':'inactive'},'HTTPException':RuntimeError}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'panel-action','exec'),namespace)
        action=namespace['_bot_action']
        def ctl(*args):
            if args[0]=='stop':self.assertFalse(alerts.read(self.control,{})['desired_running'])
            return 0,''
        with patch.object(alerts,'command',return_value='old'):
            namespace['systemctl']=ctl;action('stop');self.assertFalse(alerts.read(self.control,{})['desired_running'])
            action('start');self.assertTrue(alerts.read(self.control,{})['desired_running'])
            action('restart');self.assertTrue(alerts.read(self.control,{})['desired_running'])
            namespace['systemctl']=lambda *args:(1,'stop failed')
            action('stop');self.assertTrue(alerts.read(self.control,{})['desired_running'])
            def explode(*args):raise OSError('stop failed')
            namespace['systemctl']=explode
            with self.assertRaises(OSError):action('stop')
            self.assertTrue(alerts.read(self.control,{})['desired_running'])
    def test_normal_recovery_still_notifies(self):
        state={}
        for now,bad in ((10000,True),(10180,True),(10500,False),(10620,False)):
            alerts.transition(state,{'proxy':bad},self.cfg,now,lambda *args:self.sent.append(args))
        self.assertEqual(len(self.sent),2)
    def test_stop_during_probe_suppresses_pending_mail(self):
        state={'observed_invocation':'old','incidents':{'service':{'since':1}},'monitoring_paused':False}
        alerts.atomic(self.control,{'desired_running':False,'stopped_invocation':'old'})
        alerts.transition(state,{'service':True},self.cfg,99999,lambda *args:self.sent.append(args))
        self.assertEqual(self.sent,[]);self.assertTrue(state['monitoring_paused'])

if __name__=='__main__':unittest.main()
