#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = '''
---
module: cassandra_users_permissions

short_description: Manage Cassandra users permissions

description:
    - modify permissions of Cassandra Users
    - requires `pip install cassandra-driver`
    - Related Docs: https://datastax.github.io/python-driver/api/cassandra/query.html
    - Related Docs: https://docs.datastax.com/en/cql/3.3/cql/cql_reference/create_role.html
    
options:

  name:
    description:
      - name of the user
    required: true
    alias: keyspace
    
  replication_class:
    description:
      - Replication strategy determines the nodes where replicas are placed.
    default: "SimpleStrategy"
    choices: ["SimpleStrategy", "NetworkTopologyStrategy"]
    
  replication_factor:
    description:
      - The total number of replicas across the cluster is referred to as the replication factor.
    required: false
    default: 1
    
  datacenters:
    description:
      - Required when replication_class=NetworkTopologyStrategy
    type: dict

  durable_writes:
      description:
      - Optionally (not recommended), bypass the commit log when writing to the keyspace by disabling durable writes .
    default: "true"
    choices: ["true", "false"]
    
  login_hosts:
    description:
      - List of hosts to login to Cassandra with
    required: true
    
  login_port:
    description:
      - Port to connect to cassandra on
    default: 9042
    
  users_dict:
    description:
      - Dict with users to login to Cassandra with
    required: true
    
  state:
    description:
      - Whether the role should exist.  When C(absent), removes
        the keyspace.
    required: false
    default: present
    choices: [ "present", "absent" ]

notes:
   - "requires cassandra-driver to be installed"

'''

EXAMPLES = '''
# Assigning permissions to users
cassandra_users_permissions:
    user_name_1:
        privilege: "CREATE"
        resource_name: "ALL KEYSPACES"
        state: present
    user_name_3:
        - privilege: "CREATE"
          resource_name: "KEYSPACE keyspace_name_1"
          state: present
    user_name_2:
        state: absent
'''

from ansible.module_utils.basic import *
import re
try:
    from cassandra.cluster import Cluster
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.query import dict_factory
except ImportError:
    module.fail_json(msg="the python cassandra-driver module is required")

CHECK_ADMIN_RIGHTS = "LIST ALL"


#Connection section start
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
#Connection section end

#Checks section start
def check_keyspace_exist(cluster, keyspace_name):
    try:
        keyspace_data = cluster.metadata.keyspaces[keyspace_name].export_as_string()
    except KeyError:
        keyspace_data = ''

    return bool(keyspace_data)


def check_table_exist(cluster, table_name_with_keyspace):
    keyspace_data = None
    keyspace_name, table_name_without_keyspace = table_name_with_keyspace.split(".")
    try:
        keyspace_data = cluster.metadata.keyspaces[keyspace_name].export_as_string()
    except Exception as e:
        module.fail_json(msg="Cannot alocate table %s because keyspace %s is not found. ERROR: '%s'" % (
            table_name_without_keyspace, keyspace_name, e))

    for item in keyspace_data.split("\n"):
        if "CREATE TABLE" in item:
            try:
                found = re.search('CREATE TABLE (.+?) \(', item).group(1)
            except AttributeError:
                found = ''
            if found == "{}.{}".format(keyspace_name, table_name_without_keyspace):
                return True
    return False


def check_function_exist(cluster, function_name_with_keyspace):
    keyspace_data = None
    keyspace_name, function_name_without_keyspace = function_name_with_keyspace.split(".")
    cleared_function_name_without_keyspace = re.sub('\(.*?\)', '', function_name_without_keyspace)
    try:
        keyspace_data = cluster.metadata.keyspaces[keyspace_name].export_as_string()
    except Exception as e:
        module.fail_json(msg="Cannot allocate function %s because keyspace %s is not found. ERROR: '%s'" % (
            cleared_function_name_without_keyspace, keyspace_name, e))

    for item in keyspace_data.split("\n"):
        if "CREATE FUNCTION" in item:
            try:
                found = re.search('CREATE FUNCTION (.+?)\(', item).group(1)
            except AttributeError:
                found = ''
            if found == "{}.{}".format(keyspace_name, cleared_function_name_without_keyspace):
                return True
    return False


def check_role_exist(cluster, role_name):
    GET_USER = "SELECT * FROM system_auth.roles WHERE role = %s LIMIT 1"
    session = cluster.connect()
    user = session.execute(GET_USER, [role_name]).one()
    if user:
        return True
    else:
        return False
#Checks section end

#Expression section start
def validate_resource_clean_arg(cluster, resource_clean_name, resource_clean_arg):
    checked = None

    if "ALL FUNCTIONS" in resource_clean_name:
        checked = True
    elif "FUNCTION" in resource_clean_name:
        checked = check_function_exist(cluster, resource_clean_arg)

    elif "KEYSPACE" in resource_clean_name:
        checked = check_keyspace_exist(cluster, resource_clean_arg)

    elif "TABLE" in resource_clean_name:
        checked = check_table_exist(cluster, resource_clean_arg)

    elif "ROLE" in resource_clean_name:
        checked = check_role_exist(cluster, resource_clean_arg)

    else:
        module.fail_json(msg="Cannot idintificate resource type: %s " % (resource_clean_name))

    return checked

def strip_privileges_and_resouces(cluster, permission):
    PRIVILEGES_LIST = ["ALL PERMISSIONS", "ALTER", "AUTHORIZE", "CREATE",
                       "DESCRIBE", "DROP", "EXECUTE", "MODIFY", "SELECT"]

    RESOURCE_LIST = ["ALL FUNCTIONS", "ALL FUNCTIONS IN KEYSPACE", "FUNCTION",
                     "ALL KEYSPACES", "KEYSPACE", "TABLE", "ALL ROLES", "ROLE"]
    try:
        privilege_clean_name = max((x for x in PRIVILEGES_LIST if x in permission.get('privilege')), key=len)
    except:
        module.fail_json(msg="Broken privilege name")

    try:
        resource_clean_name = max((x for x in RESOURCE_LIST if x in permission.get('resource_name')), key=len)
    except:
        module.fail_json(msg="Broken resource name")

    resource_clean_arg = re.sub(resource_clean_name, '', permission.get('resource_name')).lstrip()
    if resource_clean_arg:
        checked = validate_resource_clean_arg(cluster, resource_clean_name, resource_clean_arg)
        if not checked:
            module.fail_json(msg="Create resource: %s, before grant or revoke permissions" % (resource_clean_arg))

    return privilege_clean_name, resource_clean_name, resource_clean_arg

def combine_expression(cluster, expression_type, recived_group_permission_state):
    expression = None
    params = None
    group_name = recived_group_permission_state.get('role')

    privilege_clean_name, resource_clean_name, resource_clean_arg = strip_privileges_and_resouces(cluster, recived_group_permission_state)

    if expression_type == "get":
        if resource_clean_arg:
            params = [group_name]
            expression = 'LIST {0} ON {1} {2} OF %s NORECURSIVE'.format(privilege_clean_name, resource_clean_name, resource_clean_arg)
        else:
            params = [group_name]
            expression = 'LIST {0} ON {1} OF %s NORECURSIVE'.format(privilege_clean_name, resource_clean_name)

    elif expression_type == "absent":
        if resource_clean_arg:
            params = [group_name]
            expression = 'REVOKE {0} ON {1} {2} FROM %s'.format(privilege_clean_name, resource_clean_name, resource_clean_arg)
        else:
            params = [group_name]
            expression = 'REVOKE {0} ON {1} FROM %s'.format(privilege_clean_name, resource_clean_name)

    elif expression_type == "present":
        if resource_clean_arg:
            params = [group_name]
            expression = 'GRANT {0} ON {1} {2} TO %s'.format(privilege_clean_name, resource_clean_name, resource_clean_arg)
        else:
            params = [group_name]
            expression = 'GRANT {0} ON {1} TO %s'.format(privilege_clean_name, resource_clean_name)

    return expression, params
#Expression section end

#Logic section end
def get_user_permission_state(cluster, recived_group_permission_state):
    expression, params = combine_expression(cluster, "get", recived_group_permission_state)
    group_name = recived_group_permission_state.get('role')
    session = cluster.connect()
    session.row_factory = dict_factory
    cassandra_group_permission_state = session.execute(expression, params).one()

    if cassandra_group_permission_state:
        cleared_resource = re.sub('[<>]', '', cassandra_group_permission_state.get('resource'))
        formated_cassandra_group_permission_state = {
                                                    'role': group_name,
                                                    'privilege': cassandra_group_permission_state.get('permission'),
                                                    'resource_name': cleared_resource,
                                                    'state': 'present'
                                                    }
    else:
        formated_cassandra_group_permission_state = {
                                                    'role': group_name,
                                                    'privilege': recived_group_permission_state.get('permission'),
                                                    'resource_name': recived_group_permission_state.get('resource_name'),
                                                    'state': 'absent'
                                                    }
    return formated_cassandra_group_permission_state

def check_changes(recived_group_permission_state, current_group_permission_state):
    recived_permission_state = recived_group_permission_state.get('state')
    current_permission_state = current_group_permission_state.get('state')

    if recived_permission_state == 'present' and current_permission_state == 'present':
        expression_type = "unchanged"
    elif recived_permission_state == 'absent' and current_permission_state == 'absent':
        expression_type = "unchanged"
    elif recived_permission_state == 'present' and current_permission_state == 'absent':
        expression_type = "present"
    elif recived_permission_state == 'absent' and current_permission_state == 'present':
        expression_type = "absent"
    else:
        module.fail_json(msg="Unable to determine the permission states. Received state: %s, current state: %s" %
                             (recived_permission_state, current_permission_state))


    return expression_type

def apply_changes(cluster, changed, recived_group_permission_state):
    expression, params = combine_expression(cluster, changed, recived_group_permission_state)
    session = cluster.connect()
    session.execute(expression, params)

#Logic section end

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
            'users_permissions_dict': {
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
            }
        },
        supports_check_mode=True
    )
    users_permissions_dict = module.params["users_permissions_dict"]
    users_dict = module.params["users_dict"]
    login_hosts = module.params["login_hosts"]
    login_port = module.params["login_port"]

    #Get connected session
    admin_user_dict = get_superuser_session(users_dict, login_hosts, login_port)
    if admin_user_dict.get("exist"):
        cluster = admin_user_dict.get("cluster")
    else:
        module.fail_json(
            msg="unable to connect to cassandra,"
                " check that you have at least one login and password from cassandra administrator user in users_dict variable"
                " and you have network access to cassandra. Exception message: %s  and code : %s and dict: %s"
                % (admin_user_dict.get("msg"), admin_user_dict.get("code"), admin_user_dict))

    # Start a magic trick
    changes_dict = dict()
    for user_name in users_permissions_dict:
        user_permissions_list = users_permissions_dict.get(user_name)
        changes = list()
        for recived_user_permission_state in user_permissions_list:

            recived_user_permission_state = dict({"role": user_name}, **recived_user_permission_state)
            current_user_permission_state = get_user_permission_state(cluster, recived_user_permission_state)

            state = check_changes(recived_user_permission_state, current_user_permission_state)

            if state != "unchanged":
                changes.append({recived_user_permission_state.get('state'):"%s %s" %(recived_user_permission_state.get('privilege'), recived_user_permission_state.get('resource_name'))})

            if not module.check_mode and state != "unchanged":
                apply_changes(cluster, state, recived_user_permission_state)


        changes_dict[user_name] = changes

    ansible_exit(changes_dict)
    # End a magic trick


if __name__ == '__main__':
    main()
