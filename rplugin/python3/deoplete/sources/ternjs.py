# pylint: disable=E0401,C0111,R0903

import re
import sys
from os import path
from deoplete.source.base import Base

# hack to import sub modules
sys.path.insert(1, path.dirname(__file__))  # noqa: E261
from deoplate_ternjs.worker import Worker


import_re = r'=?\s*require\(["\'"][\w\./-]*$|\s+from\s+["\'][\w\./-]*$'
import_pattern = re.compile(import_re)


def _search_tern_project_dir(current, cwd):
    directory = current
    while True:
        parent = path.dirname(directory[:-1])

        if not parent:
            raise ValueError('There is no .tern-project')
            # return cwd

        if path.isfile(path.join(directory, '.tern-project')):
            return directory

        directory = parent


def completion_icon(type):
    _type = '(obj)'
    if type is None or type == '?':
        _type = '(?)'
    elif type.startswith('fn('):
        _type = '(fn)'
    elif type.startswith('['):
        _type = '(' + type + ')'
    elif type == 'number':
        _type = '(num)'
    elif type == 'string':
        _type = '(str)'
    elif type == 'bool':
        _type = '(bool)'

    return _type


def type_doc(rec):
    tp = rec.get('type')
    result = rec.get('doc', ' ')
    if tp and tp != '?':
        result = tp + '\n' + result
    return result


class Source(Base):

    def __init__(self, vim):
        super(Source, self).__init__(vim)

        self.name = 'ternjs'
        self.mark = '[ternjs]'
        self.input_pattern = (r'\.\w*$|^\s*@\w*$|' + import_re)
        self.rank = 900
        self.debug_enabled = False
        self.filetypes = ['javascript']
        if 'tern#filetypes' in vim.vars:
            self.filetypes.extend(vim.vars['tern#filetypes'])

        self.last_failed = 0

        self._tern_last_length = 0

        self._disabled = False
        try:
            self._project_directory = _search_tern_project_dir(
                vim.eval("expand('%:p:h')"),
                vim.eval('getcwd()')
            )
        except Exception:
            self._disabled = True

        self.__worker = None

    def on_init(self, ctx):
        if self._disabled:
            return

        tern_timeout = 1
        if self.vim.eval('exists("g:tern_request_timeout")'):
            tern_timeout = float(self.vim.eval('g:tern_request_timeout'))

        self.__worker = Worker(
            self._project_directory,
            self.vim.vars['deoplete#sources#ternjs#tern_bin'] or 'tern',
            logger=self,
            tern_timeout=tern_timeout,
        )

    def on_event(self, context):
        if self._disabled:
            return

        if context['event'] == 'VimLeavePre':
            self.__worker.stop_server()

    def relative_file(self):
        filename = self.vim.eval("expand('%:p')")
        return filename[len(self._project_directory) + 1:]

    def completation(self, pos, file_changed):
        if self._disabled:
            return

        data = self.__worker.get_candidates(
            self.vim.current.buffer,
            pos,
            self.vim.eval("line('.')") - 1,
            file_changed,
            self.relative_file(),
        )
        if not data:
            return []

        completions = []
        for rec in data['completions']:
            icon = completion_icon(rec.get('type'))
            abbr = None

            if (icon == '(fn)'):
                abbr = rec.get('type', '').replace('fn', rec['name'], 1)
            else:
                abbr = rec['name']

            completions.append({
                'menu': '[ternjs] ',
                'kind': icon,
                'word': rec['name'],
                'abbr': abbr,
                'info': type_doc(rec),
                'dup': 1,
            })

        return completions

    def get_complete_position(self, context):
        m = import_pattern.search(context['input'])
        if m:
            # need to tell from what position autocomplete as
            # needs to autocomplete from start quote return that
            return re.search(r'["\']', context['input']).start()

        m = re.search(r'\w*$', context['input'])
        return m.start() if m else -1

    def gather_candidates(self, context):
        line = context['position'][1]
        col = context['complete_position']
        pos = {"line": line - 1, "ch": col}

        # Update autocomplete position need to send the position
        # where cursor is because the position is the start of
        # quote
        m = import_pattern.search(context['input'])
        if m:
            pos['ch'] = m.end()

        file_changed = 'TextChanged' in context['event'] or self._tern_last_length != len(self.vim.current.buffer)
        try:
            result = self.completation(pos, file_changed) or []
        except Exception as e:
            import traceback
            _, _, tb = sys.exc_info()
            filename, lineno, funname, line = traceback.extract_tb(tb)[-1]
            extra = '{}:{}, in {}\n    {}'.format(filename, lineno, funname, line)
            self.vim.err_write('Ternjs Error: {}\n{}\n'.format(e, extra))

            return []

        return result
