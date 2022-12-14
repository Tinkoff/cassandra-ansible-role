import subprocess
import json
import urllib.request
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--n', help="backup name")
parser.add_argument('--c', help="time channel")
parser.add_argument('--u', help="username for ping in message like @sreduty")
args = parser.parse_args()


status_check = subprocess.Popen(['medusa', 'status', f'--backup-name={args.n}'],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
status_stdout, status_stderr = status_check.communicate()
status = status_stdout.decode('utf-8').split("\n")

verify_check = subprocess.Popen(['medusa', 'verify', f'--backup-name={args.n}'],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
verify_stdout, verify_stderr = verify_check.communicate()
verify = verify_stdout.decode('utf-8').split("\n")

result = status + verify
prep_result = "\n".join(result)

conditionsSetURL = "https://mattermost.domain.com/hooks/hook_id"
newConditions = {"text": f'{args.u}\n{prep_result}'  , "username": "Cassandra_Backup", "channel": args.c}
proxy_host = 'proxy.domain.com:8080'

params = json.dumps(newConditions).encode('utf8')
req = urllib.request.Request(conditionsSetURL, data=params, headers={'content-type': 'application/json'})
req.set_proxy(proxy_host, 'http')
req.set_proxy(proxy_host, 'https')
response = urllib.request.urlopen(req)
