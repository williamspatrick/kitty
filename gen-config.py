#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2021, Kovid Goyal <kovid at kovidgoyal.net>

import inspect
import os
import pprint
import re
import textwrap
from typing import (
    Any, Callable, Dict, List, Set, Tuple, Union, get_type_hints
)

from kitty.conf.types import Definition, MultiOption, Option, unset


def atoi(text: str) -> str:
    return f'{int(text):08d}' if text.isdigit() else text


def natural_keys(text: str) -> Tuple[str, ...]:
    return tuple(atoi(c) for c in re.split(r'(\d+)', text))


def generate_class(defn: Definition, loc: str) -> Tuple[str, str]:
    class_lines: List[str] = []
    tc_lines: List[str] = []
    a = class_lines.append
    t = tc_lines.append
    a('class Options:')
    t('class Parser:')
    choices = {}
    imports: Set[Tuple[str, str]] = set()
    tc_imports: Set[Tuple[str, str]] = set()

    def type_name(x: type) -> str:
        ans = x.__name__
        if x.__module__ and x.__module__ != 'builtins':
            imports.add((x.__module__, x.__name__))
        return ans

    def option_type_as_str(x: Any) -> str:
        if hasattr(x, '__name__'):
            return type_name(x)
        ans = repr(x)
        ans = ans.replace('NoneType', 'None')
        return ans

    def option_type_data(option: Union[Option, MultiOption]) -> Tuple[Callable, str]:
        func = option.parser_func
        if func.__module__ == 'builtins':
            return func, func.__name__
        th = get_type_hints(func)
        rettype = th['return']
        typ = option_type_as_str(rettype)
        if isinstance(option, MultiOption):
            typ = typ[typ.index('[') + 1:-1]
            typ = typ.replace('Tuple', 'Dict', 1)
        return func, typ

    is_mutiple_vars = {}
    option_names = set()

    def parser_function_declaration(option_name: str) -> None:
        t('')
        t(f'    def {option_name}(self, val: str, ans: typing.Dict[str, typing.Any]) -> None:')

    for option in sorted(defn.iter_all_options(), key=lambda a: natural_keys(a.name)):
        option_names.add(option.name)
        parser_function_declaration(option.name)
        if isinstance(option, MultiOption):
            mval: Dict[str, Dict[str, Any]] = {'macos': {}, 'linux': {}, '': {}}
            func, typ = option_type_data(option)
            for val in option:
                if val.add_to_default:
                    gr = mval[val.only]
                    for k, v in func(val.defval_as_str):
                        gr[k] = v
            is_mutiple_vars[option.name] = typ, mval
            sig = inspect.signature(func)
            tc_imports.add((func.__module__, func.__name__))
            if len(sig.parameters) == 1:
                t(f'        for k, v in {func.__name__}(val):')
                t(f'            ans["{option.name}"][k] = v')
            else:
                t(f'        for k, v in {func.__name__}(val, ans["{option.name}"]):')
                t(f'            ans["{option.name}"][k] = v')
            continue

        if option.choices:
            typ = 'typing.Literal[{}]'.format(', '.join(repr(x) for x in option.choices))
            ename = f'choices_for_{option.name}'
            choices[ename] = typ
            typ = ename
            func = str
        else:
            func, typ = option_type_data(option)
            try:
                params = inspect.signature(func).parameters
            except Exception:
                params = {}
            if 'dict_with_parse_results' in params:
                t(f'        {func.__name__}(val, ans)')
            else:
                t(f'        ans[{option.name!r}] = {func.__name__}(val)')
            if func.__module__ != 'builtins':
                tc_imports.add((func.__module__, func.__name__))

        defval = repr(func(option.defval_as_string))
        if option.macos_defval is not unset:
            md = repr(func(option.macos_defval))
            defval = f'{md} if is_macos else {defval}'
            imports.add(('kitty.constants', 'is_macos'))
        a(f'    {option.name}: {typ} = {defval}')
        if option.choices:
            t('        val = val.lower()')
            t(f'        if val not in self.choices_for_{option.name}:')
            t(f'            raise ValueError(f"The value {{val}} is not a valid choice for {option.name}")')
            t(f'        ans["{option.name}"] = val')
            t('')
            t(f'    choices_for_{option.name} = frozenset({option.choices!r})')

    for option_name, (typ, mval) in is_mutiple_vars.items():
        a(f'    {option_name}: {typ} = ' '{}')

    for parser, aliases in defn.deprecations.items():
        for alias in aliases:
            parser_function_declaration(alias)
            tc_imports.add((parser.__module__, parser.__name__))
            t(f'        {parser.__name__}({alias!r}, val, ans)')

    action_parsers = {}

    def resolve_import(ftype: str) -> str:
        if '.' in ftype:
            fmod, ftype = ftype.rpartition('.')[::2]
        else:
            fmod = f'{loc}.options.utils'
        imports.add((fmod, ftype))
        return ftype

    for aname, action in defn.actions.items():
        option_names.add(aname)
        action_parsers[aname] = func = action.parser_func
        th = get_type_hints(func)
        rettype = th['return']
        typ = option_type_as_str(rettype)
        typ = typ[typ.index('[') + 1:-1]
        a(f'    {aname}: typing.List[{typ}] = []')
        for imp in action.imports:
            resolve_import(imp)
        for fname, ftype in action.fields.items():
            ftype = resolve_import(ftype)
            a(f'    {fname}: {ftype} = ' '{}')
        parser_function_declaration(aname)
        t(f'        for k in {func.__name__}(val):')
        t(f'            ans[{aname!r}].append(k)')
        tc_imports.add((func.__module__, func.__name__))

    a('')
    a('    def __init__(self, options_dict: typing.Optional[typing.Dict[str, typing.Any]] = None) -> None:')
    a('        if options_dict is not None:')
    a('            for key in option_names:')
    a('                setattr(self, key, options_dict[key])')

    a('')
    a('    @property')
    a('    def _fields(self) -> typing.Tuple[str, ...]:')
    a('        return option_names')

    a('')
    a('    def __iter__(self) -> typing.Iterator[str]:')
    a('        return iter(self._fields)')

    a('')
    a('    def __len__(self) -> int:')
    a('        return len(self._fields)')

    a('')
    a('    def _copy_of_val(self, name: str) -> typing.Any:')
    a('        ans = getattr(self, name)')
    a('        if isinstance(ans, dict):\n            ans = ans.copy()')
    a('        elif isinstance(ans, list):\n            ans = ans[:]')
    a('        return ans')

    a('')
    a('    def _asdict(self) -> typing.Dict[str, typing.Any]:')
    a('        return {k: self._copy_of_val(k) for k in self}')

    a('')
    a('    def _replace(self, **kw: typing.Any) -> "Options":')
    a('        ans = Options()')
    a('        for name in self:')
    a('            setattr(ans, name, self._copy_of_val(name))')
    a('        for name, val in kw.items():')
    a('            setattr(ans, name, val)')
    a('        return ans')

    a('')
    a('    def __getitem__(self, key: typing.Union[int, str]) -> typing.Any:')
    a('        k = option_names[key] if isinstance(key, int) else key')
    a('        try:')
    a('            return getattr(self, k)')
    a('        except AttributeError:')
    a('            pass')
    a('        raise KeyError(f"No option named: {k}")')

    a('')
    a('')
    a('defaults = Options()')
    for option_name, (typ, mval) in is_mutiple_vars.items():
        a(f'defaults.{option_name} = {mval[""]!r}')
        if mval['macos']:
            imports.add(('kitty.constants', 'is_macos'))
            a('if is_macos:')
            a(f'    defaults.{option_name}.update({mval["macos"]!r}')
        if mval['macos']:
            imports.add(('kitty.constants', 'is_macos'))
            a('if not is_macos:')
            a(f'    defaults.{option_name}.update({mval["linux"]!r}')

    for aname, func in action_parsers.items():
        a(f'defaults.{aname} = [')
        only: Dict[str, List[Tuple[str, Callable]]] = {}
        for sc in defn.iter_all_maps(aname):
            if not sc.add_to_default:
                continue
            text = sc.parseable_text
            if sc.only:
                only.setdefault(sc.only, []).append((text, func))
            for val in func(text):
                a(f'    {val!r},')
        a(']')
        if only:
            imports.add(('kitty.constants', 'is_macos'))
            for cond, items in only.items():
                cond = 'is_macos' if cond == 'macos' else 'not is_macos'
                a(f'if {cond}:')
                for (text, func) in items:
                    for val in func(text):
                        a(f'    defaults.{aname}.append({val!r})')

    t('')
    t('')
    t('def create_result_dict() -> typing.Dict[str, typing.Any]:')
    t('    return {')
    for oname in is_mutiple_vars:
        t(f'        {oname!r}: {{}},')
    for aname in defn.actions:
        t(f'        {aname!r}: [],')
    t('    }')

    t('')
    t('')
    t(f'actions = frozenset({tuple(defn.actions)!r})')
    t('')
    t('')
    t('def merge_result_dicts(defaults: typing.Dict[str, typing.Any], vals: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:')
    t('    ans = {}')
    t('    for k, v in defaults.items():')
    t('        if isinstance(v, dict):')
    t('            ans[k] = merge_dicts(v, vals.get(k, {}))')
    t('        elif k in actions:')
    t('            ans[k] = v + vals.get(k, [])')
    t('        else:')
    t('            ans[k] = vals.get(k, v)')
    t('    return ans')
    tc_imports.add(('kitty.conf.utils', 'merge_dicts'))

    t('')
    t('')
    t('parser = Parser()')
    t('')
    t('')
    t('def parse_conf_item(key: str, val: str, ans: typing.Dict[str, typing.Any]) -> bool:')
    t('    func = getattr(parser, key, None)')
    t('    if func is not None:')
    t('        func(val, ans)')
    t('        return True')
    t('    return False')

    preamble = ['# generated by gen-config.py DO NOT edit', '# vim:fileencoding=utf-8', '']
    a = preamble.append

    def output_imports(imports: Set, add_module_imports: bool = True) -> None:
        a('import typing')
        seen_mods = {'typing'}
        mmap: Dict[str, List[str]] = {}
        for mod, name in imports:
            mmap.setdefault(mod, []).append(name)
        for mod in sorted(mmap):
            names = sorted(mmap[mod])
            lines = textwrap.wrap(', '.join(names), 100)
            if len(lines) == 1:
                s = lines[0]
            else:
                s = '\n    '.join(lines)
                s = f'(\n    {s}\n)'
            a(f'from {mod} import {s}')
            if add_module_imports and mod not in seen_mods:
                a(f'import {mod}')
                seen_mods.add(mod)

    output_imports(imports)
    a('')
    if choices:
        a('if typing.TYPE_CHECKING:')
        for name, cdefn in choices.items():
            a(f'    {name} = {cdefn}')
        a('else:')
        for name in choices:
            a(f'    {name} = str')

    a('')
    a('option_names = (  # {{''{')
    a(' ' + pprint.pformat(tuple(sorted(option_names, key=natural_keys)))[1:] + '  # }}''}')
    class_def = '\n'.join(preamble + ['', ''] + class_lines)

    preamble = ['# generated by gen-config.py DO NOT edit', '# vim:fileencoding=utf-8', '']
    a = preamble.append
    output_imports(tc_imports, False)

    return class_def, '\n'.join(preamble + ['', ''] + tc_lines)


def write_output(loc: str, defn: Definition) -> None:
    cls, tc = generate_class(defn, loc)
    with open(os.path.join(*loc.split('.'), 'options', 'types.py'), 'w') as f:
        f.write(cls + '\n')
    with open(os.path.join(*loc.split('.'), 'options', 'parse.py'), 'w') as f:
        f.write(tc + '\n')


def main() -> None:
    from kitty.options.definition import definition
    write_output('kitty', definition)
    from kittens.diff.options.definition import definition as kd
    write_output('kittens.diff', kd)


if __name__ == '__main__':
    main()