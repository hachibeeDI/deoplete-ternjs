import re
import json
import subprocess
import platform
from os import path, environ
from pathlib import PurePath
from urllib import request
from urllib.error import HTTPError
from typing import Any, Dict

from deoplete.logger import LoggingMixin

opener = request.build_opener(request.ProxyHandler({}))
is_windows = platform.system() == "Windows"


def _create_logger():
    from logging import (
        getLogger,
        FileHandler,
        DEBUG,
    )

    logger = getLogger(__name__)
    logger.setLevel(DEBUG)
    handler = FileHandler('tern-deoplete.log')
    handler.setLevel(DEBUG)
    logger.addHandler(handler)
    return logger


def buffer_slice(buf, pos, end):
    text = ''
    while pos < len(buf):
        text += buf[pos] + '\n'
        pos += 1
    return text


def full_buffer(current_buffer):
    return buffer_slice(
        current_buffer,
        0,
        len(current_buffer),
    )


class Worker(LoggingMixin):

    def __init__(self, project_directory, tern_command, logger=None, tern_timeout=1):
        self.localhost = (is_windows and '127.0.0.1') or 'localhost'
        self.port = None
        self._project_directory = project_directory
        self.proc = None
        self.logger = logger or _create_logger()

        self._tern_arguments = '--persistent'
        self._tern_timeout = tern_timeout
        self._tern_first_request = False
        self._trying_to_start = False

        self._tern_command = tern_command

    def start_server(self):
        if self._trying_to_start:
            return

        if not self._tern_command:
            self.logger.error("tern command doesn't ready")
            return None

        self._trying_to_start = True
        portFile = path.join(self._project_directory, '.tern-port')
        # TODO: support --no-port-file option
        if path.isfile(portFile):
            with open(portFile, 'r') as f:
                self.port = int(f.read())
                # FIXME: do we really need return in here?
                # logics around self.port looks weird it looks also using as flag for something
                # return

        env = environ.copy()
        file_current = PurePath(__file__)
        node_modules_bin = path.abspath(str(
            file_current / '..' / '..' / '..' / '..' / '..' / '..' / 'node_modules' / '.bin'
        ))
        if not path.exists(node_modules_bin):
            raise ValueError('npm install in deoplete-tern plz')

        env['PATH'] += ':' + node_modules_bin

        self.proc = subprocess.Popen(
            self._tern_command + ' ' + self._tern_arguments,
            cwd=self._project_directory,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True
        )
        output = ""

        # FIXME: needs more proper way to handle process
        while True:
            line = self.proc.stdout.readline().decode('utf-8')
            if not line:
                self.logger.error('Failed to start server' + (output and ':\n' + output))
                self._trying_to_start = False
                return None

            self.logger.debug(line)
            match = re.match('Listening on port (\\d+)', line)
            if match:
                self.port = int(match.group(1))
                return
            else:
                raise ValueError('tern process returns invalid value. plz make sure your .tern-project')

    def stop_server(self):
        if self.proc is None:
            return

        self.proc.stdin.close()
        self.proc.wait()
        self.proc = None

    def make_request(self, doc, silent) -> Dict[str, Any]:
        payload = json.dumps(doc).encode('utf-8')
        try:
            req = opener.open(
                'http://' + self.localhost + ':' + str(self.port) + '/',
                payload,
                self._tern_timeout,
            )
            result = req.read()
            self.logger.debug(result)
            return json.loads(result.decode('utf-8'))
        except HTTPError as error:
            message = error.read()
            self.logger.error(message)
            raise

    def get_candidates(self, current_buffer, pos, line, file_changed, filename_relative, silent=False) -> Dict[str, Any]:
        if self.port is None:
            self.logger.debug("server haven't started")
            self.start_server()

        self.logger.debug('try get candidates with: ', line)

        file_length = len(current_buffer)

        files = []
        if not file_changed and self._tern_first_request:
            fname = filename_relative
        elif file_length > 250:
            f = self.buffer_fragment(current_buffer, line, filename_relative)

            pos = {'line': pos['line'] - f['offsetLines'], 'ch': pos['ch']}
            fname = '#0'
            files = [f]
        else:
            self._tern_first_request = True
            files = [{
                'type': 'full',
                'name': filename_relative,
                'text': full_buffer(current_buffer),
            }]
            fname = '#0'

        query = {
            'type': 'completions',
            'types': True,
            'docs': True,
            'lineCharPositions': True,
            'omitObjectPrototype': False,
            'sort': False,
        }
        doc = {'query': query, 'files': files}

        query['file'] = fname
        query['end'] = pos

        return self.make_request(doc, silent)

    def buffer_fragment(self, current_buffer, line, relative_file):
        min_indent = None
        start = None

        for i in range(max(0, line - 50), line):
            if not re.match('.*\\bfunction\\b', current_buffer[i]):
                continue
            indent = len(re.match('^\\s*', current_buffer[i]).group(0))
            if min_indent is None or indent <= min_indent:
                min_indent = indent
                start = i

        if start is None:
            start = max(0, line - 50)

        end = min(len(current_buffer) - 1, line + 20)

        return {
            'type': 'part',
            'name': relative_file,
            'text': buffer_slice(current_buffer, start, end),
            'offsetLines': start,
        }


    def __del__(self):
        self.stop_server()
