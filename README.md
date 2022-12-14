
Cassandra
=========

Роль больше для вдохновления, но мы ее успешно используем в проде.

Backup
------
### При бекапе расходуется место на диске, обязательно проводить в разное время с компакшеном.

Для настройки бэкапирования - тег `backup`

Восстановление бэкапа через ansible-console командой на все ноды кластера
`sudo medusa restore-node --in-place --backup-name=2022_11_12_01_full`
Документация по восстановлению: https://github.com/thelastpickle/cassandra-medusa/blob/master/docs/Restoring-a-single-node.md

Управление кейспейсами
----------------------
Для управления кейспесами - тэг `keyspaces`

Управление пользователями и правами
-----------------------------------
Для управления ролями, пользователями - тэг `users`

Управление джобавми компакшена
------------------------------
Для управления кронтабом nodetool compact - тэг `cron_compact`

Example infra role
------------------
В папке example infra role содержится пример репозитория для развертывания кассандра кластера.

Role Variables
--------------

# Cassandra
```
cluster_name: "sometests"
cassandra_port: "9042"
login_hosts: "{{ ansible_play_hosts }}"
```
# Enable or Disable heap-dump if there's an OOM (default is 'true')
```
enable_heapdump: false
```
# Create cassandra users
```
cassandra_users_dict:
    max:
        password: "qwerty"
        superuser: true
        enable_login: true
        state: present
    adm:
        password: "adm"
        superuser: true
        enable_login: true
        state: present
    mr_bad:
        state: absent
```
# Create cassandra keyspace
```
cassandra_keyspaces:
    keyspace_name_1:
        replication_class: "SimpleStrategy"
        replication_factor: 2
        state: present
    keyspace_name_2:
        replication_class: "NetworkTopologyStrategy"
        datacenter: 
            dc01: 3
            dc02: 3
            dc03: 3
        state: present
    keyspace_name_3:
        state: absent
```
# Create cassandra groups
```
cassandra_groups:
    group_name_1:
        superuser: false
        state: present
    group_name_2:
        state: absent
```
# Assigning permissions to groups
```
cassandra_groups_permissions:
    group_name_1:
        privilege: "CREATE"
        resource_name: "ALL KEYSPACES"
        state: present
    group_name_3:
        privilege: "CREATE"
        resource_name: "KEYSPACE keyspace_name_1"
        state: present
    group_name_2:
        state: absent
```

# Adding users to groups
```
cassandra_adding_users_to_groups:
    group_name_1:
        - "username_1"
        - "username_2"
        - "username_3"
    group_name_3:
        - "username_3"
        - "username_1"
        - "username_2"
```

# Add custom compactions in crontab for exclude tables in keyspace
 Исключить таблицу table3 в keyspace_test2 из процесса compact, только таблицы table1,table2 будут обработаны
```
cassandra_compact_cron_cmd: >
  nodetool compact -s keyspace_test &&
  nodetool compact -s keyspace_test2 &&
  nodetool compact -s keyspace_test2 table1 &&
  nodetool compact -s keyspace_test2 table2
```

# Schedule repair for just created keyspace to cassandra reaper
```
cassandra_reaper:            
  autoschedule_repair: true #if set true, repair will be automatically scheduled at the moment of creation
  hostname: cassandra-reaper.domain.com
  port: 80
  protocol: http
  username: admin
  password: admin
  cluster_name: invest_qa
  settings:  #you can read about it there - http://cassandra-reaper.io/docs/configuration/reaper_specific/ 
    segment_count_per_node: 16
    intensity: 1
    incremental_repair: false
    schedule_days_between: 7
    repair_thread_count: 4
    repair_parallelism: DATACENTER_AWARE
    owner: invest-sre
     
#also you may override settings and scheduling(in case when it disable on global) on keyspace scope
#e.g.
cassandra_keyspaces:
  my_awesome_ks:
    replication_class: "NetworkTopologyStrategy"
    datacenter:
      m1: 3
      ds: 3
    state: present
    cassandra_reaper: #<-there
      schedule_repair: true
      settings:
        segment_count_per_node: 14
        intensity: 0.8
        incremental_repair: true
        schedule_days_between: 5
        repair_thread_count: 6
        repair_parallelism: PARALLEL
```
Dependencies
------------

* java
