# -*- coding: utf-8 -*-
#
# @Author: Florian Briegel (briegel@mpia.de)
# @Date: 2021-08-18
# @Filename: proxy.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import logging
import asyncio

import json
from types import SimpleNamespace
from typing import Any, Callable, Optional, Awaitable
from itertools import chain

from clu import AMQPClient, AMQPReply, command_parser
from clu.tools import CommandStatus
from clu.model import Model


class ProxyException(Exception):
    """Base proxy exception"""

    def __init__(self, argv):

        super(ProxyException, self).__init__(argv)


class ProxyPlainMessagException(ProxyException):
    """Plain message formed exception string"""

    def __init__(self, *argv):

        super(ProxyException, self).__init__(argv)


class _ProxyMethod:
    __slots__ = (
        "_amqpc",
        "_consumer",
        "_command",
    )
    
    def __init__(self, amqpc, consumer, command):
        self._amqpc = amqpc
        self._consumer = consumer
        self._command = command
    
    def __getattr__(self, item) -> "_ProxyMethod":
        return _ProxyMethod(".".join((self._consumer, item)), func=self.func)
    
    async def __call__(
        self, 
        *args,
        blocking: bool = True,
        callback: Callable[[Any], Awaitable[None]] = None,
        timeout = 1.4142,
        **kwargs,
    ):
        opts=list(chain.from_iterable(('--'+k, v) for k, v in kwargs.items()))
        command =  await asyncio.wait_for(self._amqpc.send_command(self._consumer, self._command.lower(), *args, *opts, callback=callback), timeout) 
        return await command if blocking else command


class Proxy:
    __slots__ = (
        "_consumer",
        "_amqpc"
    )
    
    def __init__(
        self, 
        consumer: str,
        amqpc: AMQPClient
    ):
        self._consumer = consumer
        self._amqpc = amqpc
    
    def __getattr__(self, command) -> _ProxyMethod:
        return _ProxyMethod(self._amqpc, self._consumer, command)
    

def _stringToException(errstr):
    """converts a string to an exception object"""
    try:
       return eval(errstr) # Maybe a bad idea - code injection
   
    except SyntaxError as e:
       return ProxyPlainMessagException(errstr)
   
    except Exception as e:
       return Exception("Unexpected exception in parsing exception string", e)


class DictObject(object):
    """converts a dict to an object
    
    Note: Ideally tthis whould be done before converting with json from string to dict.
    
      import json
      from collections import namedtuple

      json.loads(data, object_hook=lambda d: namedtuple('X', d.keys())(*d.values()))
      
      
      import json
      from types import SimpleNamespace

      data = '{"name": "John Smith", "hometown": {"name": "New York", "id": 123}}'

      # Parse JSON into an object with attributes corresponding to dict keys.
      x = json.loads(data, object_hook=lambda d: SimpleNamespace(**d))
      print(x.name, x.hometown.name, x.hometown.id)
    
    Note: or https://github.com/Infinidat/munch
    
    """
    def __str__(self):
        return str(self._dict)
    
    def __init__(self, d):
        self._dict=d
        for a, b in d.items():
            if isinstance(b, (list, tuple)):
               setattr(self, a, [DictObject(x) if isinstance(x, dict) else x for x in b])
            else:
               setattr(self, a, DictObject(b) if isinstance(b, dict) else b)


async def invoke(*argv, raw=False, **kwargs):
    """invokes one or many commands in parallel
    
       On error it throws an exception if one of the commands fails as a dict with an exception or None for every command. 
       For a single command it throws only an exception.
    
       Parameters
       ----------
       
       raw
           True: returns the body of the finish reply as a dict
           False: returns the body of the finish reply as a DictObject
       
    """
    
    if len(argv) > 1:
        ret = await asyncio.gather(*[asyncio.create_task(cmd) for cmd in argv])
        errors=[]
        for r in ret:
            hasErrors=False
            if r.status.did_fail:
                hasErrors=True
                errors.append(_stringToException(r.replies[-1].body['error']))
            else:
                errors.append(None)
        if hasErrors: raise ProxyException(errors)
        if raw: return [r.replies[-1].body for r in ret]
        else: return [DictObject(r.replies[-1].body) for r in ret]
    else:
        ret = await argv[0]
        if ret.status.did_fail:
            raise _stringToException(ret.replies[-1].body['error'])
        else:
            if raw: return ret.replies[-1].body
            else: return DictObject(ret.replies[-1].body)

async def unpack(cmd, *argv, **kwargs):
    """ invokes one command and unpacks every parameter from the body of the finish reply
    
        It uses pythons list unpacking mechanism PEP3132, be warned if you dont use it the correct way.
       
        >>> a, b, c = [1, 2, 3]
        >>> a
        1
        
        >>> a = [1, 2, 3]
        >>> a
        [1, 2, 3]
        
        >>> a, b = [1, 2, 3]
        Traceback (most recent call last):
        File "<stdin>", line 1, in <module>
        ValueError: too many values to unpack (expected 2)

        >>> a, *b = [1, 2, 3]
        >>> a
        1
        >>> b
        [2, 3]

        Parameters
        ----------
       
        argv
           return only the parameters from argv
    """
    
    ret = await invoke(cmd, raw=True)
    if len(ret) == 0: return
    elif len(ret) == 1: return list(ret.values())[0] # Maybe we should check if argv is not empty and throw an exception
    elif len(argv) > 1: return [ret[i] for i in argv]
    else: return list(ret.values())

