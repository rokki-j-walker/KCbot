# -*- coding: utf-8 -*-
"""dotnet.py — чтение и пересборка .NET-сборок (PE + метаданные CLI).

Нужен только для патчера (см. patcher.py): вставить `call KCDbg.Tick()` в
начало `Director.Update`.

Что умеет:
  * парсинг PE (DOS/COFF/Optional/секции/CLI header)
  * metadata root и заголовки потоков (#~, #Strings, #US, #Blob, #GUID)
  * каталог таблиц `#~`: счётчики строк, размеры индексов, размеры строк
    по схеме ECMA-335 II.22 (все стандартные таблицы 0x00..0x2C)
  * чтение конкретных строк: TypeDef, MethodDef, TypeRef, MemberRef,
    AssemblyRef, NestedClass
  * пересборка `#~` с добавленными строками и изменённым RVA
  * резервирование новых строк в #Strings/#Blob

Не умеет (и не нужно): семантический разбор IL, сериализацию произвольных
таблиц — нетронутые таблицы копируются как есть.
"""
import struct

# ---------------------------------------------------------------- PE-заголовки

class Section(object):
    __slots__ = ('name', 'vs', 'va', 'rs', 'pr', 'ch', 'hdr_off')

    def __repr__(self):
        return ('<Section %s va=0x%x vs=%d raw=0x%x rs=%d ch=0x%x>'
                % (self.name, self.va, self.vs, self.pr, self.rs, self.ch))


class PE(object):
    """Минимальный PE-ридер/писатель (только то, что нужно патчеру)."""

    def __init__(self, data):
        self.data = bytearray(data)
        d = self.data
        if d[:2] != b'MZ':
            raise ValueError('не MZ')
        self.e_lfanew = struct.unpack_from('<I', d, 0x3C)[0]
        if bytes(d[self.e_lfanew:self.e_lfanew + 4]) != b'PE\0\0':
            raise ValueError('не PE')
        self.coff = self.e_lfanew + 4
        (self.machine, self.nsec, self.ts, self.psym, self.nsym,
         self.optsz, self.chars) = struct.unpack_from('<HHIIIHH', d, self.coff)
        self.opt = self.coff + 20
        self.magic = struct.unpack_from('<H', d, self.opt)[0]
        self.pe32plus = self.magic == 0x20b
        if self.pe32plus:
            raise ValueError('PE32+ не поддерживается')
        self.file_align = struct.unpack_from('<I', d, self.opt + 36)[0]
        self.sect_align = struct.unpack_from('<I', d, self.opt + 32)[0]
        self.size_of_image_off = self.opt + 56
        self.ndd_off = self.opt + 92          # NumberOfRvaAndSizes
        self.dd_off = self.opt + 96           # данные каталоги
        self.sec_off = self.opt + self.optsz

        self.sections = []
        for i in range(self.nsec):
            o = self.sec_off + i * 40
            s = Section()
            s.hdr_off = o
            s.name = bytes(d[o:o + 8]).rstrip(b'\0').decode('ascii', 'replace')
            (s.vs, s.va, s.rs, s.pr, _prl, _pln, _nrl, _nln, s.ch) = \
                struct.unpack_from('<IIIIIIHHI', d, o + 8)
            self.sections.append(s)

    # ---- секции / RVA ----
    def section_of_rva(self, rva):
        for s in self.sections:
            span = max(s.vs, s.rs)
            if s.va <= rva < s.va + span:
                return s
        return None

    def rva_to_off(self, rva):
        s = self.section_of_rva(rva)
        if s is None:
            return None
        return s.pr + (rva - s.va)

    def off_to_rva(self, off):
        for s in self.sections:
            if s.pr <= off < s.pr + s.rs:
                return s.va + (off - s.pr)
        return None

    # ---- CLI header ----
    # CLI header лежит в данных каталогах (индекс 14) - НЕ путать с полем
    # MetaData.RVA/Size внутри самого CLI header (смещение +8/+12).
    @property
    def cli_off(self):
        rva, _size = struct.unpack_from('<II', self.data, self.dd_off + 14 * 8)
        return self.rva_to_off(rva)

    @property
    def cli_rva(self):
        return struct.unpack_from('<II', self.data, self.dd_off + 14 * 8)[0]

    def metadata_size_off(self):
        """Смещение поля MetaData.Size внутри CLI header."""
        return self.cli_off + 12

    # ---- размеры ----
    def size_of_image(self):
        return struct.unpack_from('<I', self.data, self.size_of_image_off)[0]

    def set_size_of_image(self, v):
        struct.pack_into('<I', self.data, self.size_of_image_off, v)


# ------------------------------------------------------------ метаданные

class Metadata(object):
    """Metadata root + потоки."""

    SIG = 0x424A5342

    def __init__(self, pe, cli_off):
        self.pe = pe
        d = pe.data
        _cb, _maj, _min, self.md_rva, self.md_size = struct.unpack_from(
            '<IHHII', d, cli_off)
        self.root = pe.rva_to_off(self.md_rva)
        if self.root is None:
            raise ValueError('metadata RVA вне секций')
        m = self.root
        sig, maj, minr, _res, vlen = struct.unpack_from('<IHHII', d, m)
        if sig != self.SIG:
            raise ValueError('не BSJB')
        self.version = bytes(d[m + 16:m + 16 + vlen]).rstrip(b'\0').decode(
            'utf8', 'replace')
        p = m + 16 + ((vlen + 3) & ~3)
        self.flags, self.nstreams = struct.unpack_from('<HH', d, p)
        p += 4
        self.streams = []           # [(name, off, size, header_off)]
        for _ in range(self.nstreams):
            off, size = struct.unpack_from('<II', d, p)
            hdr = p
            p += 8
            end = d.index(b'\0', p)
            name = bytes(d[p:end]).decode('ascii')
            p = (end + 1 + 3) & ~3
            self.streams.append([name, off, size, hdr])

    def stream(self, name):
        for s in self.streams:
            if s[0] == name:
                return s
        return None

    def set_stream(self, name, off, size):
        for s in self.streams:
            if s[0] == name:
                s[1], s[2] = off, size
                struct.pack_into('<II', self.pe.data, s[3], off, size)
                return True
        return False


# ------------------------------------------------------- #~ каталог таблиц

# Типы полей строк таблиц (ECMA-335 II.22).
#   ('h', 'str'|'guid'|'blob')           - индекс в куче
#   ('i', table_id)                      - индекс в простую таблицу
#   ('c', 'имя', [таблицы], bits)        - кодированный индекс

# кодированные индексы (ECMA-335 II.24.2.6)
CODED_INDEXES = {
    'TypeDefOrRef':            ([2, 1, 0x1B], 2),
    'HasConstant':             ([4, 8, 0x17], 2),
    'HasCustomAttribute':      ([6, 4, 1, 2, 8, 9, 10, 0, 0xE, 0x17, 0x14,
                                 0x11, 0x1A, 0x1B, 0x20, 0x23, 0x26, 0x27,
                                 0x28, 0x2A, 0x2C, 0x2B], 5),
    'HasFieldMarshal':         ([4, 8], 1),
    'HasDeclSecurity':         ([2, 6, 0x20], 2),
    'MemberRefParent':         ([2, 1, 0x1A, 6, 0x1B], 3),
    'HasSemantics':            ([0x14, 0x17], 1),
    'MethodDefOrRef':          ([6, 10], 1),
    'MemberForwarded':         ([4, 6], 1),
    'Implementation':          ([0x26, 0x23, 0x27], 2),
    'CustomAttributeType':     ([None, None, 6, 10, None], 3),
    'ResolutionScope':         ([0, 0x1A, 0x23, 1], 2),
    'TypeOrMethodDef':         ([2, 6], 1),
}

# schema[таблица] = список полей; каждое поле: (имя, код)
# код: 'u1'|'u2'|'u4' | ('h', heap) | ('i', table) | ('c', имя_кодированного)
def _t(*fields):
    return list(fields)


TABLE_SCHEMA = {
    0x00: _t(('Generation', 'u2'), ('Name', 'h:str'), ('Mvid', 'h:guid'),
             ('EncId', 'h:guid'), ('EncBaseId', 'h:guid')),
    0x01: _t(('ResolutionScope', 'c:ResolutionScope'), ('Name', 'h:str'),
             ('Namespace', 'h:str')),
    0x02: _t(('Flags', 'u4'), ('Name', 'h:str'), ('Namespace', 'h:str'),
             ('Extends', 'c:TypeDefOrRef'), ('FieldList', 'i:4'),
             ('MethodList', 'i:6')),
    0x04: _t(('Flags', 'u2'), ('Name', 'h:str'), ('Signature', 'h:blob')),
    0x06: _t(('RVA', 'u4'), ('ImplFlags', 'u2'), ('Flags', 'u2'),
             ('Name', 'h:str'), ('Signature', 'h:blob'), ('ParamList', 'i:8')),
    0x08: _t(('Flags', 'u2'), ('Sequence', 'u2'), ('Name', 'h:str')),
    0x09: _t(('Class', 'i:2'), ('Interface', 'c:TypeDefOrRef')),
    0x0A: _t(('Class', 'c:MemberRefParent'), ('Name', 'h:str'),
             ('Signature', 'h:blob')),
    0x0B: _t(('Type', 'u1'), ('Padding', 'u1'), ('Parent', 'c:HasConstant'),
             ('Value', 'h:blob')),
    0x0C: _t(('Parent', 'c:HasCustomAttribute'),
             ('Type', 'c:CustomAttributeType'), ('Value', 'h:blob')),
    0x0D: _t(('Parent', 'c:HasFieldMarshal'), ('NativeType', 'h:blob')),
    0x0E: _t(('Action', 'u2'), ('Parent', 'c:HasDeclSecurity'),
             ('PermissionSet', 'h:blob')),
    0x0F: _t(('PackingSize', 'u2'), ('ClassSize', 'u4'), ('Parent', 'i:2')),
    0x10: _t(('Offset', 'u4'), ('Field', 'i:4')),
    0x11: _t(('Signature', 'h:blob')),
    0x12: _t(('Parent', 'i:2'), ('EventList', 'i:0x14')),
    0x14: _t(('EventFlags', 'u2'), ('Name', 'h:str'),
             ('EventType', 'c:TypeDefOrRef')),
    0x15: _t(('Parent', 'i:2'), ('PropertyList', 'i:0x17')),
    0x17: _t(('Flags', 'u2'), ('Name', 'h:str'), ('Type', 'h:blob')),
    0x18: _t(('Semantics', 'u2'), ('Method', 'i:6'),
             ('Association', 'c:HasSemantics')),
    0x19: _t(('Class', 'i:2'), ('MethodBody', 'c:MethodDefOrRef'),
             ('MethodDeclaration', 'c:MethodDefOrRef')),
    0x1A: _t(('Name', 'h:str')),
    0x1B: _t(('Signature', 'h:blob')),
    0x1C: _t(('MappingFlags', 'u2'), ('MemberForwarded', 'c:MemberForwarded'),
             ('ImportName', 'h:str'), ('ImportScope', 'i:0x1A')),
    0x1D: _t(('RVA', 'u4'), ('Field', 'i:4')),
    0x20: _t(('HashAlgId', 'u4'), ('MajorVersion', 'u2'),
             ('MinorVersion', 'u2'), ('BuildNumber', 'u2'),
             ('RevisionNumber', 'u2'), ('Flags', 'u4'),
             ('PublicKey', 'h:blob'), ('Name', 'h:str'), ('Culture', 'h:str')),
    0x21: _t(('Processor', 'u4')),
    0x22: _t(('OSPlatformID', 'u4'), ('OSMajorVersion', 'u4'),
             ('OSMinorVersion', 'u4')),
    0x23: _t(('MajorVersion', 'u2'), ('MinorVersion', 'u2'),
             ('BuildNumber', 'u2'), ('RevisionNumber', 'u2'), ('Flags', 'u4'),
             ('PublicKeyOrToken', 'h:blob'), ('Name', 'h:str'),
             ('Culture', 'h:str'), ('HashValue', 'h:blob')),
    0x24: _t(('Processor', 'u4'), ('AssemblyRef', 'i:0x23')),
    0x25: _t(('OSPlatformID', 'u4'), ('OSMajorVersion', 'u4'),
             ('OSMinorVersion', 'u4'), ('AssemblyRef', 'i:0x23')),
    0x26: _t(('Flags', 'u4'), ('Name', 'h:str'), ('HashValue', 'h:blob')),
    0x27: _t(('Flags', 'u4'), ('TypeDefId', 'u4'), ('TypeName', 'h:str'),
             ('TypeNamespace', 'h:str'), ('Implementation', 'c:Implementation')),
    0x28: _t(('Offset', 'u4'), ('Flags', 'u4'), ('Name', 'h:str'),
             ('Implementation', 'c:Implementation')),
    0x29: _t(('NestedClass', 'i:2'), ('EnclosingClass', 'i:2')),
    0x2A: _t(('Number', 'u2'), ('Flags', 'u2'), ('Owner', 'c:TypeOrMethodDef'),
             ('Name', 'h:str')),
    0x2B: _t(('Method', 'c:MethodDefOrRef'), ('Instantiation', 'h:blob')),
    0x2C: _t(('Owner', 'i:0x2A'), ('Constraint', 'c:TypeDefOrRef')),
}

TABLE_NAMES = {
    0x00: 'Module', 0x01: 'TypeRef', 0x02: 'TypeDef', 0x04: 'Field',
    0x06: 'MethodDef', 0x08: 'Param', 0x09: 'InterfaceImpl',
    0x0A: 'MemberRef', 0x0B: 'Constant', 0x0C: 'CustomAttribute',
    0x0D: 'FieldMarshal', 0x0E: 'DeclSecurity', 0x0F: 'ClassLayout',
    0x10: 'FieldLayout', 0x11: 'StandAloneSig', 0x12: 'EventMap',
    0x14: 'Event', 0x15: 'PropertyMap', 0x17: 'Property',
    0x18: 'MethodSemantics', 0x19: 'MethodImpl', 0x1A: 'ModuleRef',
    0x1B: 'TypeSpec', 0x1C: 'ImplMap', 0x1D: 'FieldRVA', 0x1E: 'ENCLog',
    0x1F: 'ENCMap', 0x20: 'Assembly', 0x21: 'AssemblyProcessor',
    0x22: 'AssemblyOS', 0x23: 'AssemblyRef', 0x24: 'AssemblyRefProcessor',
    0x25: 'AssemblyRefOS', 0x26: 'File', 0x27: 'ExportedType',
    0x28: 'ManifestResource', 0x29: 'NestedClass', 0x2A: 'GenericParam',
    0x2B: 'MethodSpec', 0x2C: 'GenericParamConstraint',
}


class Tables(object):
    """Каталог таблиц #~: счётчики, размеры, разметка."""

    def __init__(self, meta):
        self.meta = meta
        self.pe = meta.pe
        self._parse()

    def _parse(self):
        d = self.pe.data
        s = self.meta.stream('#~')
        if s is None:
            raise ValueError('нет потока #~')
        self.off = self.meta.root + s[1]
        self.size = s[2]
        t = self.off
        (_res, self.major, self.minor, self.heapsizes, _r2) = \
            struct.unpack_from('<IBBBB', d, t)
        self.valid, self.sorted_mask = struct.unpack_from('<QQ', d, t + 8)
        self.str_idx = 4 if self.heapsizes & 1 else 2
        self.guid_idx = 4 if self.heapsizes & 2 else 2
        self.blob_idx = 4 if self.heapsizes & 4 else 2

        p = t + 24
        self.counts = {}
        for i in range(64):
            if self.valid & (1 << i):
                self.counts[i] = struct.unpack_from('<I', d, p)[0]
                p += 4
        self.data_off = p

        # разметка: (смещение, размер_строки)
        self.layout = {}
        cur = p
        for tid in sorted(self.counts):
            rowlen = self.row_size(tid)
            self.layout[tid] = (cur, rowlen)
            cur += rowlen * self.counts[tid]
        self.data_end = cur

    # ---- размеры ----
    def _idx_size(self, spec):
        if spec.startswith('h:'):
            kind = spec[2:]
            return {'str': self.str_idx, 'guid': self.guid_idx,
                    'blob': self.blob_idx}[kind]
        if spec.startswith('i:'):
            tid = int(spec[2:], 0)
            return 4 if self.counts.get(tid, 0) >= 65536 else 2
        if spec.startswith('c:'):
            name = spec[2:]
            tables, bits = CODED_INDEXES[name]
            mx = 0
            for tid in tables:
                if tid is None:
                    continue
                mx = max(mx, self.counts.get(tid, 0))
            return 4 if mx >= (1 << (16 - bits)) else 2
        raise ValueError(spec)

    def row_size(self, tid):
        schema = TABLE_SCHEMA.get(tid)
        if schema is None:
            raise ValueError('нет схемы таблицы 0x%02X (%s)'
                             % (tid, TABLE_NAMES.get(tid, '?')))
        n = 0
        for _name, spec in schema:
            if spec in ('u1', 'u2', 'u4'):
                n += {'u1': 1, 'u2': 2, 'u4': 4}[spec]
            else:
                n += self._idx_size(spec)
        return n

    def row_off(self, tid, index):
        """Смещение строки index (0-based) в файле."""
        base, rowlen = self.layout[tid]
        return base + rowlen * index

    def read_fields(self, tid, index):
        """Прочитать строку -> dict имя_поля -> значение (int)."""
        d = self.pe.data
        p = self.row_off(tid, index)
        out = {}
        for name, spec in TABLE_SCHEMA[tid]:
            if spec == 'u1':
                v = d[p]; p += 1
            elif spec == 'u2':
                v = struct.unpack_from('<H', d, p)[0]; p += 2
            elif spec == 'u4':
                v = struct.unpack_from('<I', d, p)[0]; p += 4
            else:
                n = self._idx_size(spec)
                v = int.from_bytes(d[p:p + n], 'little'); p += n
            out[name] = v
        return out

    def write_field_u4(self, tid, index, field, value):
        """Перезаписать 4-байтовое поле строки (например MethodDef.RVA)."""
        p = self.row_off(tid, index)
        for name, spec in TABLE_SCHEMA[tid]:
            if name == field:
                if spec != 'u4':
                    raise ValueError('поле %s не u4' % field)
                struct.pack_into('<I', self.pe.data, p, value)
                return
            if spec in ('u1', 'u2', 'u4'):
                p += {'u1': 1, 'u2': 2, 'u4': 4}[spec]
            else:
                p += self._idx_size(spec)
        raise ValueError('нет поля %s' % field)

    # ---- кучи ----
    def heap_off(self, kind):
        name = {'str': '#Strings', 'blob': '#Blob', 'guid': '#GUID'}[kind]
        s = self.meta.stream(name)
        return self.meta.root + s[1], s[2]

    def read_str(self, idx):
        base, size = self.heap_off('str')
        if idx == 0:
            return ''
        d = self.pe.data
        end = d.index(b'\0', base + idx)
        return bytes(d[base + idx:end]).decode('utf8', 'replace')

    def read_blob(self, idx):
        base, size = self.heap_off('blob')
        d = self.pe.data
        p = base + idx
        b0 = d[p]
        if b0 < 0x80:
            ln, p = b0, p + 1
        elif b0 < 0xC0:
            ln = ((b0 & 0x3F) << 8) | d[p + 1]; p += 2
        else:
            ln = ((b0 & 0x1F) << 24) | (d[p + 1] << 16) | \
                 (d[p + 2] << 8) | d[p + 3]; p += 4
        return bytes(d[p:p + ln])

    # ---- удобные выборки ----
    def find_typedef(self, full_name):
        """Вернуть индекс TypeDef по 'Namespace.Name' или 'Name'."""
        for i in range(self.counts.get(2, 0)):
            r = self.read_fields(2, i)
            ns = self.read_str(r['Namespace'])
            nm = self.read_str(r['Name'])
            full = (ns + '.' + nm) if ns else nm
            if full == full_name or nm == full_name:
                return i
        return None

    def typedef_method_range(self, td_index):
        """(от, до) индексы MethodDef, принадлежащие TypeDef."""
        n_m = self.counts.get(6, 0)
        r = self.read_fields(2, td_index)
        start = r['MethodList']            # 1-based в спецификации
        # конец = начало методов следующего TypeDef (или конец таблицы)
        if td_index + 1 < self.counts.get(2, 0):
            nxt = self.read_fields(2, td_index + 1)
            end = nxt['MethodList']
        else:
            end = n_m + 1
        return start, end

    def find_method(self, td_index, name):
        lo, hi = self.typedef_method_range(td_index)
        for i in range(lo, hi):
            if i - 1 < 0 or i - 1 >= self.counts.get(6, 0):
                continue
            r = self.read_fields(6, i - 1)
            if self.read_str(r['Name']) == name:
                return i - 1
        return None
