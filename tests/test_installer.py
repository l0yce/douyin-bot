"""Exercise package-manager plans; tests must never install or start services."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipIf(os.name=='nt','Linux Bash installer')
class InstallerPlans(unittest.TestCase):
    def plan(self,manager):
        result=subprocess.run(['bash',str(ROOT/'scripts/install.sh'),'--plan','--package-manager',manager],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('不启动机器人发送消息',result.stdout)
        return result.stdout
    def test_apt_dependencies(self):
        output=self.plan('apt')
        self.assertIn('apt-get install -y',output)
        self.assertIn('python3-venv',output)
        self.assertIn('playwright install --with-deps firefox',output)
    def test_dnf_dependencies(self):
        output=self.plan('dnf')
        self.assertIn('dnf install -y',output)
        self.assertIn('xorg-x11-server-Xvfb',output)
        self.assertNotIn('--with-deps',output)
    def test_yum_dependencies(self):
        output=self.plan('yum')
        self.assertIn('yum install -y',output)
        self.assertNotIn('apt-get',output)
    def test_invalid_manager_is_rejected(self):
        result=subprocess.run(['bash',str(ROOT/'scripts/install.sh'),'--plan','--package-manager','invalid'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)

if __name__=='__main__':unittest.main()
