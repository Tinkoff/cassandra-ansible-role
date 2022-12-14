#!/usr/bin/python
# -*- coding: utf-8 -*-


DOCUMENTATION = '''
---
module: cassandra_users

short_description: Manage Cassandra Users

description:
    - Add/remove Cassandra Users
    - requires `pip install cassandra-driver`
    - Related Docs: https://datastax.github.io/python-driver/api/cassandra/query.html
    - Related Docs: https://docs.datastax.com/en/cql/3.3/cql/cql_reference/create_role.html

author: "m.o.ivanov & s.p.byvshev"

inspirer: https://github.com/sadams/ansible-module-cassandra

Module common options:

  login_hosts:
    description:
      - List of hosts to login to Cassandra with
    required: true

  users_dict:
    description:
      - Dict with users to login to Cassandra with
    required: true

  login_port:
    description:
      - Port to connect to cassandra on
    default: 9042
    required: false

Module per user options: 

  name:
    description:
      - name of the role to add or remove
    required: true
    alias: role

  password:
    description:
      - Set the role's password. Setting password will always elicit a 'change' (even if is the same).
    required: true

  superuser:
    description:
      - Create the user as a superuser?
    required: false
    default: False

  state:
    description:
      - Whether the role should exist.  When C(absent), removes the role.
    required: false
    default: present
    choices: [ "present", "absent" ]

notes:
   - "requires cassandra-driver to be installed"

'''

EXAMPLES_VARS = '''
#EXAMPLE TASK
- name: add users 
  cassandra_users:
    users_dict: "{{ cassandra_users_dict }}"
    login_hosts: "{{ ansible_default_ipv4.address }}"
    login_port: "{{ cassandra_port | default('9042') }}"

#EXAMPLE VARS
# Presented User And Absented User
cassandra_users_dict:
    username1:
        password: qwerty 
        superuser: true
        enable_login: True
        state: present
    username2:
        state: absent


#Execute on node, connect to same node
cassandra_login_hosts: {{ ansible_default_ipv4.address }}

#Execute on node, connect to one of cluster node
cassandra_login_hosts: {{ ansible_play_hosts }}

#Execute on node, connect to one of prt-prod-api node
cassandra_login_hosts: ["1.1.1.1", "example.test.com", "10.2.2.2"] 

#Cassandra listening port if you want a change it
cassandra_port: "9042"

'''
from ansible.module_utils.basic import *
try:
    from bcrypt import gensalt
    from bcrypt import hashpw
except ImportError:
    module.fail_json(msg="the python bcrypt module is required")
try:
    from cassandra.cluster import Cluster
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.query import dict_factory, SimpleStatement
    from cassandra import ConsistencyLevel

except ImportError:
    module.fail_json(msg="the python cassandra-driver module is required")

CHECK_ADMIN_RIGHTS = SimpleStatement(
    "LIST ALL",
    consistency_level=ConsistencyLevel.QUORUM
)
GET_USER = SimpleStatement(
    "SELECT * FROM system_auth.roles WHERE role = %s LIMIT 1",
    consistency_level=ConsistencyLevel.QUORUM
)
ABSENT_USER = SimpleStatement(
    "DROP ROLE %s",
    consistency_level=ConsistencyLevel.QUORUM
)
UPDATE_USER = SimpleStatement(
    "UPDATE system_auth.roles SET salted_hash = %s, can_login = %s, is_superuser = %s WHERE role = %s",
    consistency_level=ConsistencyLevel.QUORUM
)
CREATE_USER = SimpleStatement(
    "CREATE ROLE %s WITH PASSWORD = %s AND LOGIN = %s AND SUPERUSER = %s",
    consistency_level=ConsistencyLevel.QUORUM
)

# Class section start

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
    :param users_dict: Dictionary with alupperl users sent to ansible module
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

#Get user info section start
def find_user(session, username):
    '''
    Check the user exist in Cassandra and get hes current parameters
    :session: Connected session with administrator rights
    :param username: User role name
    :return: Current user state and parameters in the form of a dictionary object
    '''
    session.row_factory = dict_factory
    user = session.execute(GET_USER, [username]).one()
    if user:
        user['state'] = "present"
    else:
        user = dict() # The lines look more the same
        user['state'] = "absent"

    return user
#Get user info section end

#Check user parameters state section start
def create_salted_hash(password):
    '''
    Create salted_hash from given password
    :param password: given password
    :return: salted_hash
    '''
    salt = gensalt(10, prefix=b"2a")
    hashed_passwd = hashpw(password.encode('UTF-8'), salt).decode('utf-8')
    return(hashed_passwd)

def compare_passwords(received_password, salted_hash):
    '''
    Compares the received password with the salted hash of the current password in the Cassandra
    :param password: New user password
    :param salted_hash: the user's current password salted hash
    :return: boolean True or False, depending on the result of password compared
    '''
    encoded_password_to_check = received_password.encode('UTF-8')
    encoded_salted_hash = salted_hash.encode('UTF-8')
    matched = hashpw(encoded_password_to_check, encoded_salted_hash) == encoded_salted_hash
    return(matched)

def simple_compare_tool(received_user, current_user, compared_object_name):

    if received_user.get(compared_object_name) == current_user.get(compared_object_name):
        return(True)
    else:
        return(False)

def compare_user_states(received_user, current_user, changes_list):
    change_type = "UNCHANGED"
    changes = list()
    finalized_user={"role" : current_user.get('role'), "state": "present"}
    if not compare_passwords(received_user.get('password'), current_user.get('salted_hash')):
        finalized_user['password'] = create_salted_hash(received_user.get('password'))
        changes.append("password")
        change_type = "UPDATE_USER"
    else:
        finalized_user['password'] = current_user.get('salted_hash')

    if not simple_compare_tool(received_user, current_user, "is_superuser"):
        finalized_user['is_superuser'] = received_user.get('is_superuser')
        changes.append("is_superuser")
        change_type = "UPDATE_USER"
    else:
        finalized_user['is_superuser'] = current_user.get('is_superuser')

    if not simple_compare_tool(received_user, current_user, "can_login"):
        finalized_user['can_login'] = received_user.get('can_login')
        changes.append("can_login")
        change_type = "UPDATE_USER"
    else:
        finalized_user['can_login'] = current_user.get('can_login')

    return(finalized_user, change_type, changes)

def check_changes(session, received_user, changes_list):
    #Incorrect state error handler
    if received_user['state'] not in ['present', 'absent']:
        module.fail_json(msg="State of received user state is not valid(Present or Absent)")

    current_user = find_user(session, received_user.get('role'))
    change_dict = {}
    if received_user.get('state') == "absent" and current_user.get('state') == "absent":
        change_dict = {"change_type": "UNCHANGED", "finalized_user" : dict()}

    elif received_user.get('state') == "absent" and current_user.get('state') == "present":
        changes_list[received_user.get('role')] = "was removed"
        change_dict = {"change_type": "ABSENT_USER", "finalized_user" : current_user}

    elif received_user.get('state') == "present" and current_user.get('state') == "absent":
        changes_list[received_user.get('role')] = received_user.keys()
        change_dict = {"change_type": "CREATE_USER", "finalized_user" : received_user}

    elif received_user.get('state') == "present" and current_user.get('state') == "present":
        finalized_user, change_type_status, changes = compare_user_states(received_user, current_user, changes_list)
        if changes:
            changes_list[received_user.get('role')] = changes
        change_dict = {"change_type": change_type_status, "finalized_user" : finalized_user}

    return change_dict

#Check user parameters state section end

#Modify user section start

def user_modifier(session, changes):
    try:
        if changes.get("change_type") == "UNCHANGED":
            pass
        elif changes.get("change_type") == "CREATE_USER":
            params = (changes.get("finalized_user").get("role"),
                      changes.get("finalized_user").get("password"),
                      changes.get("finalized_user").get("can_login"),
                      changes.get("finalized_user").get("is_superuser"))
            session.execute(CREATE_USER, params)

        elif changes.get("change_type") == "ABSENT_USER":
            params = (changes.get("finalized_user").get("role"))
            session.execute(ABSENT_USER, [params])

        elif changes.get("change_type") == "UPDATE_USER":
            params = (changes.get("finalized_user").get("password"),
                      changes.get("finalized_user").get("can_login"),
                      changes.get("finalized_user").get("is_superuser"),
                      changes.get("finalized_user").get("role"))
            session.execute(UPDATE_USER, params)

        else:
            module.fail_json(
                msg="Unexpected change_type: %s, for user: %s" % (changes.get("change_type"), changes.get("finalized_user").get("role")))
    except Exception as e:
        module.fail_json(msg="Unable to find change_type: %s, for user: %s, error_message: %s, params: %s" % (
            changes.get("change_type"), changes.get("finalized_user").get("role"), e, params))


#Update,Create or Delete user section end

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

    users_dict = module.params["users_dict"]
    login_hosts = module.params["login_hosts"]
    login_port = module.params["login_port"]

    #Get connected session
    admin_user_dict = get_superuser_session(users_dict, login_hosts, login_port)
    if admin_user_dict.get("exist") is True:
        session = admin_user_dict.get("session")
    else:
        module.fail_json(
            msg="unable to connect to cassandra,"
                " check that you have at least one login and password from cassandra administrator user in users_dict variable"
                " and you have network access to cassandra. Exception message: %s  and code : %s and dict: %s"
                % (admin_user_dict.get("msg"), admin_user_dict.get("code"), admin_user_dict))

    #Start a magic trick
    changes_list = dict()
    for received_user in users_dict:
        received_user = dict({"role": received_user}, **users_dict[received_user])
        change_dict = check_changes(session, received_user, changes_list)
        if not module.check_mode:
            user_modifier(session, change_dict)

    ansible_exit(changes_list)
    #End a magic trick


if __name__ == '__main__':
    main()
