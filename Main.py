from Utils.UtilsServer import get_servers
import time

servers = get_servers()

for s in servers:
    print(s.name, s.path)

servers[1].start()
time.sleep(5)
print(servers[1].is_running())
servers[1].stop()
time.sleep(5)
print(servers[1].is_running())