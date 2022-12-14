Common-cassandra
=========

Роль для настройки Нод под приложение Common-cassandra

```buildoutcfg
cp ansible.cfg.example ansible.cfg
ansible-galaxy install -r requirements.yml
ansibple-playbook -i inventory/prod playbook.yml
```


Role Variables
--------------

* user_manager - сервисная учетная запись, от которой управление приложением
