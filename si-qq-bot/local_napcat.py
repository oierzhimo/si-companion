"""Read only the already bound bot's loopback credential, never other accounts."""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExistingNapCat:
    bot_id: str
    endpoint: str = 'ws://127.0.0.1:3001/'
    token: str = field(default='', repr=False)


def _read(path, limit):
    if not path.is_file() or path.stat().st_size>limit:
        raise ValueError('unavailable local configuration')
    raw=path.read_bytes()
    if len(raw)>limit:
        raise ValueError('unavailable local configuration')
    return json.loads(raw.decode('utf-8-sig'))


def existing_connection(data_dir, *, config_dir=None):
    state=_read(Path(data_dir)/'si_qq_core_state.json',1_000_000)
    if not isinstance(state,dict) or not state.get('admin_openid'):
        raise ValueError('existing administrator binding required')
    bot=state.get('app_id')
    if not isinstance(bot,str) or not re.fullmatch(r'[0-9]{1,20}',bot):
        raise ValueError('existing bot identifier required')
    if config_dir is None:
        config_dir=Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'NapCatQQ Desktop/components/NapCatQQ/config'
    root=Path(config_dir).resolve()
    path=root/f'onebot11_{bot}.json'
    if path.resolve().parent!=root:
        raise ValueError('local configuration outside expected directory')
    config=_read(path,256_000)
    if not isinstance(config,dict) or not isinstance(config.get('network'),dict):
        raise ValueError('unavailable local configuration')
    servers=config.get('network',{}).get('websocketServers',[])
    matches=[]
    for server in servers if isinstance(servers,list) else []:
        if not isinstance(server,dict):continue
        if (server.get('enable') is not True or server.get('host')!='127.0.0.1'
                or server.get('port') not in (3001,'3001')
                or server.get('path','/') not in ('','/')):continue
        token=server.get('token')
        if not isinstance(token,str) or not re.fullmatch(r'[^\x00-\x20\x7f]{1,512}',token):continue
        matches.append(ExistingNapCat(bot_id=bot,token=token))
    if len(matches)!=1:
        raise ValueError('expected one authenticated loopback connection')
    return matches[0]
