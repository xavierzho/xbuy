# Multi-server frpc startup

Use this when the home master needs to connect to several public servers at once.

## Home Windows master

1. Keep the Moutai WebUI running so master WS listens on `127.0.0.1:8765`.
2. Edit `server_addrs.txt`, one server per line:

```text
220.197.14.204
1.2.3.4
5.6.7.8:7000
```

3. Double-click:

```text
start_frpc_multi_home.bat
```

The script starts one `frpc.exe` window per server and maps:

```text
home 127.0.0.1:8765 -> each server 127.0.0.1:18765
```

## Each Linux server

Run the server side first:

```bash
cd /root/5.1茅台/frp_mt_ws
chmod +x start_server_all.sh
./start_server_all.sh
```

or start only frps:

```bash
./frps -c frps.toml
```

Then the worker on that server connects to:

```bash
cd /root/5.1茅台
../scripts/start_distributed_worker.sh ws://127.0.0.1:18765
```

## Notes

- Every public server can reuse remote port `18765`; they are different machines.
- The home machine can run many frpc clients at the same time; they all read local `127.0.0.1:8765`.
- Server security group must allow `7000/tcp` from the home master.
