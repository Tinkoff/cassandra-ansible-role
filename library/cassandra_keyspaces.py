#!/usr/bin/env python3
import requests
from typing import Union
from ansible.module_utils.basic import *

try:
    from cassandra.cluster import Cluster
    from cassandra.auth import PlainTextAuthProvider
except ImportError:
    cassandra_dep_found = False
else:
    cassandra_dep_found = True

CHECK_ADMIN_RIGHTS = "LIST ALL"


class CassandraReaper:
    def __init__(self, hostname: str, port: int, proto: str, username: str, password: str, cluster_name: str , reaper_settings: dict):
        self.hostname = hostname
        self.port = port
        self.proto = proto
        self.username = username
        self.password = password
        self.cluster_name = cluster_name
        self.reaper_settings = reaper_settings
        self.jwt = self._auth(username, password)

    def _auth(self, username: str, password: str) -> str:
        s = requests.Session()
        s.post(url='{}://{}:{}/login'.format(self.proto, self.hostname, self.port), data={'username': self.username, 'password': self.password})
        jwt = s.get('{}://{}:{}/jwt'.format(self.proto, self.hostname, self.port)).content.decode("utf-8")

        return jwt

    @staticmethod
    def _create_dummy_table(session: Cluster.connect, keyspace: str):
        session.execute("CREATE TABLE {}.dummy_cassandra_reaper_table(id UUID PRIMARY KEY) ;".format(keyspace))

    @staticmethod
    def _drop_dummy_table(session: Cluster.connect, keyspace: str):
        session.execute("DROP TABLE {}.dummy_cassandra_reaper_table ;".format(keyspace))

    def schedule(self, session: Cluster.connect, keyspace: str) -> Union[bool, requests.models.Response]:
        status = False
        self._create_dummy_table(session, keyspace)
        headers = {'Authorization': 'Bearer {}'.format(str(self.jwt))}
        params = {'clusterName': self.cluster_name,
                'keyspace': keyspace,
                'owner': self.reaper_settings['owner'],
                'segmentCountPerNode': self.reaper_settings['segment_count_per_node'],
                'repairParallelism': self.reaper_settings['repair_parallelism'],
                'intensity': self.reaper_settings['intensity'],
                'incrementalRepair': self.reaper_settings['incremental_repair'],
                'scheduleDaysBetween': self.reaper_settings['schedule_days_between'],
                'repairThreadCount': self.reaper_settings['repair_thread_count']}
        r = requests.post('{}://{}:{}/repair_schedule'.format(self.proto, self.hostname, self.port), headers=headers, params=params)
        if r.status_code == 201:
            status = True
        self._drop_dummy_table(session, keyspace)

        return status, r.content


class KeyspaceOperator:
    def __init__(self, name, session, request_timeout):
        self.name = name
        self.session = session
        self.request_timeout = request_timeout

    def __alter_or_create(self, op_type, replication_params, durable_write):
        query_template = ("{} KEYSPACE {} "
                          "WITH REPLICATION = {{"
                          "'class': '{replication_class}', "
                          "{} }} "
                          "AND DURABLE_WRITES=%s ;")

        if replication_params['replication_class'] == 'SimpleStrategy':
            replication_propertie = "'replication_factor': '{replication_factor}'".format(**replication_params)
        elif replication_params['replication_class'] == 'NetworkTopologyStrategy':
            dc_list = []
            for k, v in replication_params['datacenter'].items():
                dc_list.append("'{}': '{}'".format(k, v))
                replication_propertie = ','.join(dc_list)
        query = query_template.format(op_type, self.name, replication_propertie, **replication_params)
        params = [durable_write]
        self.session.execute(query, parameters=params, timeout=self.request_timeout)

    def update(self, replication_params, durable_write='true'):
        self.__alter_or_create('ALTER', replication_params, durable_write)

    def create(self, replication_params, durable_write='true'):
        self.__alter_or_create('CREATE', replication_params, durable_write)

    def delete(self):
        query = 'DROP KEYSPACE %s ;' % self.name
        self.session.execute(query, timeout=self.request_timeout)

    def get_info(self):
        query = "SELECT replication, durable_writes FROM system_schema.keyspaces " \
                "WHERE keyspace_name=%s;"
        params = [self.name]
        raw_result = None
        try:
            raw_result = self.session.execute(query, parameters=params, timeout=self.request_timeout)[0]
        except Exception:
            return raw_result
        result = {'replica': {},
                  'durable_writes': None}
        for k, v in raw_result.replication.items():
            result['replica'].update({k: v})
        result['durable_writes'] = 'true' if raw_result.durable_writes else 'false'
        return result


class KeyspaceComparer:
    def __init__(self, name, session, params, request_timeout):
        self.name = name
        self.keyspace_operation = KeyspaceOperator(name, session, request_timeout)
        self.params = params
        self.replication_class = params.get('replication_class')
        self.datacenter = params.get('datacenter')
        self.replication_factor = str(params.get('replication_factor'))
        self.state = params.get('state')
        self.request_timeout = request_timeout

    def _agregate_params(self):
        """
        Aggregate self.params and current replication info
        :return dict: with params for easily keyspace comparing
        """
        current_replication_class = None
        current_dc_amount = None
        params_dc_amount = len(self.datacenter) if self.datacenter else None
        diff_between_rep_factor = None
        diff_between_dc = None
        current_state = 'absent'
        current_params = None
        if self.keyspace_operation.get_info():
            current_params = self.keyspace_operation.get_info()['replica']
            current_replication_class = current_params['class']
            current_state = 'present'
            if 'NetworkTopologyStrategy' in current_replication_class:
                current_dc_amount = len(current_params) - 1
            if self.datacenter and 'NetworkTopologyStrategy' in current_replication_class:
                if (any(current_params.get(k) != str(v) for k, v in self.datacenter.items())
                        or current_dc_amount != params_dc_amount):
                    diff_between_dc = True
            if (self.replication_factor and "SimpleStrategy" in current_replication_class
                    and self.replication_factor != current_params.get('replication_factor')):
                diff_between_rep_factor = True
        return {'current_state': current_state,
                'current_params': current_params,
                'current_replication_class': current_replication_class,
                'diff_between_dc': diff_between_dc,
                'diff_between_rep_factor': diff_between_rep_factor
                }

    def _check_keyspace_diff(self, params):
        """
        Check difference between existing keyspace and needed to create
        :param params: dict with params for easily keyspace comparing
        :return diff: boolean diff state
        """
        diff = False
        if (self.replication_class not in params['current_replication_class']
                or params['diff_between_dc']
                or params['diff_between_rep_factor']):
            diff = True
        return diff

    def get_report(self):
        """
        Generate a report based on comparing state and keyspaces params
        :return report: dict with operation name(or error) and description
        """
        params = self._agregate_params()
        current_state = params['current_state']
        report = {}
        if self.replication_class not in ["SimpleStrategy", "NetworkTopologyStrategy"]:
            report['error'] = "Given unexpected replication class for %s" % self.name
        elif self.state not in ['present', 'absent']:
            report['error'] = ("Given unexpected keyspace state: %s. "
                               "Supported states: [absent, present]" % self.state)
        elif self.state == 'absent' and current_state == 'absent':
            report['warning'] = "Can't delete %s. Because keyspace doesn't exist" % self.name
        elif self.state == 'present' and current_state == 'present':
            if self._check_keyspace_diff(params):
                report['update'] = "UPDATE %s with params: %s" % (self.name, self.params)
            else:
                report['warning'] = "Keyspace %s with params %s already exist" % (self.name, self.params)
        elif self.state == 'present' and current_state == 'absent':
            report['create'] = 'CREATE %s with params %s' % (self.name, self.params)
        elif self.state == 'absent' and current_state == 'present':
            report['delete'] = 'DROP %s' % self.name
        return report


# Connection section start
def initiate_session(login_hosts, login_port, username, password):
    """
    Verify that the user still has administrator rights and create a session with the credentials of that user
    :param login_hosts: Cassandra host or hosts list
    :param login_port: Cassandra connection port
    :param username: verified user
    :param password: password of the verified user
    :return: Dictionary with administrator credentials, connected cassandra session and boolean code
    """
    result = {}
    try:
        auth_provider = PlainTextAuthProvider(username=username, password=password)
        cluster = Cluster(login_hosts, auth_provider=auth_provider, protocol_version=4, port=login_port)
        session = cluster.connect()
        check_result = session.execute(CHECK_ADMIN_RIGHTS).one()
        result["cluster"] = cluster
        result["session"] = session
        result["admin_user_cred"] = {'username': username, 'password': password, 'check_result': check_result}
        result["exist"] = True
    except Exception as e:
        result["msg"] = e
        result["exist"] = False
    return result


def get_superuser_session(users_dict, login_hosts, login_port):
    """
    Search for an administrator user in the users_dict dictionary and return connected cassandra session
    :param users_dict: Dictionary with all users sent to ansible module
    :param login_hosts: Cassandra host or hosts list
    :param login_port: Cassandra connection port
    :return: Dictionary with administrator credentials, connected cassandra session and boolean code
    """
    user_validator = [('can_login', True), ('state', 'present'), ('is_superuser', True)]
    error_user_lite = {"exist": False, "msg": "Not find valid *SUPER* user in users_dict or CASSANDRA Die", "users_dict": users_dict}
    for user in users_dict:
        if all(x in users_dict[user].items() for x in user_validator):
            superuser_session = initiate_session(login_hosts, login_port, user, users_dict[user]["password"])
            if superuser_session["exist"]:
                return superuser_session
    try:
        return {"exist": False, "msg": "Not find valid *SUPER* user in users_dict or CASSANDRA Die", "error": superuser_session}
    except:
        return error_user_lite


# Connection section end


def repair_keyspace(keyspaces_list):
    cmd = ['nodetool', 'repair', keyspaces_list]
    module.run_command(cmd)


def apply_configuration(keyspaces_dict, reaper_dict, adm_session, request_timeout):
    """
    Call KeyspaceOperator methods based on report
    :param keyspaces_dict: dict with configuration for cassandra keyspaces
    :param adm_session: cassandra session with administrator permissions
    :param request_timeout: query execution timeout in seconds
    :return changes: list with reports about applied configurations
    """
    changes = {}
    cassandra_reaper_schedule_status_message = {
        True: "cassandra reaper job for {} keyspace has been successfully scheduled",
        False: "something went wrong when scheduling {} keyspace in cassandra reaper API, details: {}"
    }
    for keyspace, params in keyspaces_dict.items():
        report = KeyspaceComparer(keyspace, adm_session, params, request_timeout).get_report()
        perform_operation = KeyspaceOperator(keyspace, adm_session, request_timeout)
        if report.get('create'):
            perform_operation.create(params)
            changes[keyspace] = report['create']
            if reaper_dict['autoschedule_repair'] is True or (params.get('cassandra_reaper') and
                                                              params['cassandra_reaper']['schedule_repair'] is True):
                if params.get('cassandra_reaper') and params['cassandra_reaper'].get('settings'):
                    reaper_settings = {**reaper_dict['settings'], **params['cassandra_reaper']['settings']}
                else:
                    reaper_settings = reaper_dict['settings']
                reaper = CassandraReaper(hostname=reaper_dict['hostname'], port=reaper_dict['port'], proto=reaper_dict['protocol'],
                                         username=reaper_dict['username'], password=reaper_dict['password'],
                                         cluster_name=reaper_dict['cluster_name'], reaper_settings=reaper_settings)
                repair_schedule_status, details = reaper.schedule(adm_session, keyspace)
                changes["{}_cassandra_reaper_job".format(keyspace)] = cassandra_reaper_schedule_status_message[repair_schedule_status].\
                    format(keyspace, details)

        elif report.get('update'):
            perform_operation.update(params)
            changes[keyspace] = report['update']
        elif report.get('delete'):
            perform_operation.delete()
            changes[keyspace] = report['delete']
        elif report.get('warning'):
            module.warn(report['warning'])
        elif report.get('error'):
            module.fail_json(msg=report['error'])
    return changes


## ANSIBLE EXIT START
def ansible_exit(changes):
    if changes:
        module.exit_json(changed=True, name=changes)
    else:
        module.exit_json(changed=False, name=changes)


## ANSIBLE EXIT END

def main():
    global module
    module = AnsibleModule(
        argument_spec={
            'keyspaces_dict': {
                'required': True,
                'no_log': True,
                'type': 'dict'
            },
            'users_dict': {
                'required': True,
                'no_log': True,
                'type': 'dict'
            },
            'login_hosts': {
                'required': True,
                'type': 'list'
            },
            'login_port': {
                'required': True,
                'type': 'int'
            },
            'request_timeout': {
                'required': True,
                'type': int
            },
            'reaper_dict': {
                'required': True,
                'type': 'dict'
            },
        },
        supports_check_mode=True
    )
    if not cassandra_dep_found:
        module.fail_json(msg="the python cassandra-driver module is required")
    keyspaces_dict = module.params["keyspaces_dict"]
    users_dict = module.params["users_dict"]
    reaper_dict = module.params["reaper_dict"]
    login_hosts = module.params["login_hosts"]
    login_port = module.params["login_port"]
    request_timeout = module.params["request_timeout"]

    admin_user_dict = get_superuser_session(users_dict, login_hosts, login_port)
    adm_session = None
    if admin_user_dict.get("exist"):
        adm_session = admin_user_dict.get("session")
    else:
        module.fail_json(
            msg="unable to connect to cassandra,"
                " check that you have at least one login and password"
                " from cassandra administrator user in users_dict variable"
                " and you have network access to cassandra. Exception message: %s  and code : %s and dict: %s"
                % (admin_user_dict.get("msg"), admin_user_dict.get("code"), admin_user_dict))

    changes = apply_configuration(keyspaces_dict, reaper_dict, adm_session, request_timeout)
    ansible_exit(changes)


if __name__ == '__main__':
    main()
