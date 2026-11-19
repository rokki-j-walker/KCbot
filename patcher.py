# -*- coding: utf-8 -*-
"""patcher.py — патчинг Assembly-CSharp.dll на чистом Python.

Заменяет tools/patch.exe (C#, Mono.Cecil) — решение D22.

Что делает патч
---------------
В начало тела метода `Director.Update()` вставляется инструкция

    call  KCDbg::Tick()

то есть строка `KCLog5.dll` подключается к Assembly-CSharp, и логгер пишет
состояние игры в `kcdbg.txt` (именно его читает bot3.py).

Как устроен пересбор (почему всё так сложно)
--------------------------------------------
Вставка требует ТРЁХ новых строк метаданных:
    AssemblyRef "KCLog5", TypeRef "KCDbg", MemberRef "Tick".
Их нельзя вставить «между» существующими, не сдвинув номера строк, на которые
ссылается существующий IL. Поэтому:

  * новые строки добавляются В КОНЕЦ своих таблиц -> номера старых не меняются;
  * таблица `#~` пересобирается целиком (копия всех таблиц +3 новые строки)
    и записывается в НОВОЕ место, т.к. вставиться в старое нельзя;
  * `#Strings` тоже пересобирается (туда добавляются 3 строки: KCDbg, KCLog5,
    Tick) и уезжает вслед за `#~`;
  * оба новых потока пишутся в секцию `.reloc`, которая последняя в файле и
    заканчивается ровно на EOF -> её просто наращиваем;
  * у `.reloc` снимается флаг DISCARDABLE, иначе загрузчик может выгрузить
    наши метаданные;
  * новое тело метода (262 байта) пишется в свободный хвост секции `.text`
    (444 байта непокрытого raw-данными) с расширением VirtualSize;
  * RVA тела метода и заголовки потоков обновляются на месте.

Ничего существующего не переносится: RVAs, номера строк и смещения всех
старых объектов остаются валидными.

Использование
-------------
    py patcher.py --status            # пропатчена ли игра
    py patcher.py --patch             # пропатчить (спросит про бэкап)
    py patcher.py --patch --no-backup # пропатчить без бэкапа
    py patcher.py --unpatch           # вернуть бэкап
"""
import argparse
import os
import struct
import sys

import dotnet
import gamepath

# ------------------------------------------------------------- константы

TEXT_SLACK_HINT = 444          # непокрытый хвост .text (см. докстринг)
MARKER = b'KCDbg'              # признак пропатченной сборки


def _u(v, n):
    return int(v).to_bytes(n, 'little')


def managed_dir(game_dir):
    return os.path.join(game_dir, 'Kingdom_Data', 'Managed')


def target_dll(game_dir):
    return os.path.join(managed_dir(game_dir), 'Assembly-CSharp.dll')


def kclog_dll(game_dir):
    return os.path.join(managed_dir(game_dir), 'KCLog5.dll')


# ------------------------------------------------------------- детект

def is_patched(path=None):
    """Пропатчена ли сборка (ищем строку KCDbg в #Strings)."""
    path = path or target_dll(gamepath.game_dir())
    try:
        with open(path, 'rb') as f:
            return MARKER in f.read()
    except OSError:
        return None            # файла нет / не читается - неизвестно


def status(game_dir=None):
    """Человеческий статус: ('ok'|'not_patched'|'missing', сообщение)."""
    game_dir = game_dir or gamepath.game_dir()
    if not game_dir:
        return 'missing', 'папка игры не задана'
    dll = target_dll(game_dir)
    if not os.path.isfile(dll):
        return 'missing', 'нет файла %s' % dll
    if not os.path.isfile(kclog_dll(game_dir)):
        return 'missing', 'нет %s (нужен для патча)' % kclog_dll(game_dir)
    if is_patched(dll):
        return 'ok', 'игра пропатчена'
    return 'not_patched', 'игра НЕ пропатчена - логгер не будет писать kcdbg.txt'


def _find_blob(heap, content):
    """Найти в #Blob запись с нужным СОДЕРЖИМЫМ. Вернуть индекс (смещение
    префикса длины) или -1.

    Простой bytes.find() НЕ годится: в куче лежит '03 00 00 01' (префикс
    длины + данные), а искать нужно структуру записи. find по '00 00 01'
    находил ложное совпадение внутри чужого blob - подпись получалась пустой.
    """
    p = 0
    n = len(heap)
    while p < n:
        b0 = heap[p]
        if b0 < 0x80:
            ln, hdr = b0, 1
        elif b0 < 0xC0:
            if p + 1 >= n:
                break
            ln, hdr = ((b0 & 0x3F) << 8) | heap[p + 1], 2
        elif b0 < 0xE0:
            if p + 2 >= n:
                break
            ln, hdr = ((b0 & 0x1F) << 16) | (heap[p + 1] << 8) | heap[p + 2], 3
        else:
            if p + 3 >= n:
                break
            ln = ((b0 & 0x0F) << 24) | (heap[p + 1] << 16) | \
                 (heap[p + 2] << 8) | heap[p + 3]
            hdr = 4
        start = p
        p += hdr
        if p + ln > n:
            break
        if heap[p:p + ln] == content:
            return start
        p += ln
    return -1


# ------------------------------------------------------------- чтение цели

class _Ctx(object):
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = bytearray(f.read())
        self.pe = dotnet.PE(self.data)
        self.md = dotnet.Metadata(self.pe, self.pe.cli_off)
        self.t = dotnet.Tables(self.md)


def _parse_kclog(path):
    """Вернуть (имя_сборки, (major,minor,build,rev), blob_сигнатуры Tick)."""
    c = _Ctx(path)
    t = c.t
    name, ver = None, (0, 0, 0, 0)
    for i in range(t.counts.get(0x20, 0)):
        r = t.read_fields(0x20, i)
        name = t.read_str(r['Name'])
        ver = (r['MajorVersion'], r['MinorVersion'],
               r['BuildNumber'], r['RevisionNumber'])
    if not name:
        raise ValueError('в %s нет Assembly' % path)
    # TypeDef KCDbg -> метод Tick
    td = t.find_typedef('KCDbg')
    if td is None:
        raise ValueError('в %s нет типа KCDbg' % path)
    mid = t.find_method(td, 'Tick')
    if mid is None:
        raise ValueError('в %s нет метода KCDbg.Tick' % path)
    sig = t.read_blob(t.read_fields(6, mid)['Signature'])
    return name, ver, bytes(sig)


# ------------------------------------------------------------- пересбор #~

def _build_tilde(t, insertions):
    """Пересобрать поток #~ с добавленными строками.

    insertions: {table_id: [(index_0based, bytes_строки), ...]}
    Строки добавляются в КОНЕЦ таблицы -> номера существующих не меняются.
    """
    d = t.pe.data
    counts = dict(t.counts)
    for tid, rows in insertions.items():
        counts[tid] += len(rows)

    # существующие размеры строк не должны измениться (пересечения 65536 нет)
    for tid in insertions:
        old = t.row_size(tid)
        # row_size использует counts из self - считаем заново вручную
        pass

    out = bytearray()
    out += bytes(d[t.off:t.off + 24])          # заголовок (Valid/Sorted те же)
    for tid in sorted(counts):
        out += struct.pack('<I', counts[tid])
    for tid in sorted(t.counts):
        base, rowlen = t.layout[tid]
        rows = [bytes(d[base + i * rowlen: base + (i + 1) * rowlen])
                for i in range(t.counts[tid])]
        for idx, rb in insertions.get(tid, []):
            rows.insert(idx, rb)
        out += b''.join(rows)
    return bytes(out), counts


def _build_strings(t, new_names):
    """Вернуть (новые_байты_кучи, {имя: смещение})."""
    base, size = t.heap_off('str')
    old = bytes(t.pe.data[base:base + size])
    offsets = {}
    buf = bytearray(old)
    for s in new_names:
        offsets[s] = len(buf)
        buf += s.encode('utf8') + b'\0'
    return bytes(buf), offsets


# ------------------------------------------------------------- тело метода

def _read_body(pe, rva):
    """Вернуть (смещение, заголовок_байты, код, фатический_флаг)."""
    off = pe.rva_to_off(rva)
    if off is None:
        raise ValueError('RVA 0x%x вне секций' % rva)
    d = pe.data
    b0 = d[off]
    if (b0 & 3) == 2:
        return off, bytes(d[off:off + 1]), bytes(d[off + 1:off + 1 + (b0 >> 2)]), False
    flags_size, maxstack, codesize, lvt = struct.unpack_from('<HHII', d, off)
    hdr = (flags_size >> 12) * 4
    if hdr < 12:
        hdr = 12
    return off, bytes(d[off:off + hdr]), bytes(d[off + hdr:off + hdr + codesize]), True


def _make_body(old_hdr, old_code, token, is_fat):
    """Новое тело: те же настройки + call token в начале."""
    call = b'\x28' + struct.pack('<I', token)
    code = call + old_code
    if not is_fat:
        if len(code) < 64:
            return bytes([((len(code) << 2) | 2) & 0xFF]) + code
        is_fat = True
    # fat: flags/size(2) MaxStack(2) CodeSize(4) LocalVarSigTok(4)
    if is_fat and len(old_hdr) >= 12:
        flags_size, maxstack, _cs, lvt = struct.unpack_from('<HHII', old_hdr, 0)
    else:
        flags_size, maxstack, lvt = 0x3, 8, 0
    flags = (flags_size & 0x0FFF) | 0x3000        # формат fat + размер 3 dword
    return struct.pack('<HHII', flags, maxstack, len(code), lvt) + code


# ------------------------------------------------------------- основное

def patch(game_dir=None, src=None, dst=None, kclog=None, log=print):
    """Пропатчить сборку. Вернуть True при успехе."""
    game_dir = game_dir or gamepath.game_dir()
    src = src or target_dll(game_dir)
    dst = dst or src
    kclog = kclog or kclog_dll(game_dir)

    if not os.path.isfile(src):
        log('!! нет файла %s' % src)
        return False
    if not os.path.isfile(kclog):
        log('!! нет логгера %s' % kclog)
        return False

    c = _Ctx(src)
    t = c.t
    pe, md = c.pe, c.md

    # --- уже пропатчена? ---
    if MARKER in bytes(pe.data):
        log('сборка уже пропатчена')
        return True

    # --- цель ---
    td = t.find_typedef('Director')
    if td is None:
        log('!! не найден тип Director')
        return False
    mid = t.find_method(td, 'Update')
    if mid is None:
        log('!! не найден метод Director.Update')
        return False
    mrow = t.read_fields(6, mid)
    old_rva = mrow['RVA']
    if not old_rva:
        log('!! у Director.Update нет тела')
        return False
    body_off, old_hdr, old_code, is_fat = _read_body(pe, old_rva)
    log('цель: Director.Update idx=%d RVA=0x%x тело=%d байт (%s)'
        % (mid, old_rva, len(old_code), 'fat' if is_fat else 'tiny'))

    # --- сборка-логгер ---
    asm_name, asm_ver, tick_sig = _parse_kclog(kclog)
    log('логгер: %s v%d.%d.%d.%d, KCDbg.Tick сигнатура=%s'
        % ((asm_name,) + asm_ver + (tick_sig.hex(),)))

    # --- куча строк: 3 новых имени ---
    new_str, str_off = _build_strings(t, ['KCDbg', asm_name, 'Tick'])
    log('#Strings: %d -> %d байт' % (t.heap_off('str')[1], len(new_str)))

    # --- сигнатура void(): ищем в #Blob, иначе добавляем ---
    bbase, bsize = t.heap_off('blob')
    blob_old = bytes(pe.data[bbase:bbase + bsize])
    sig_idx = _find_blob(blob_old, b'\x00\x00\x01')     # void()
    new_blob = None
    if sig_idx < 0:
        new_blob = bytearray(blob_old) + b'\x03\x00\x00\x01'
        sig_idx = len(blob_old)
        log('#Blob: добавлена сигнатура void()')
    else:
        log('#Blob: сигнатура void() уже есть (индекс %d)' % sig_idx)

    # --- новые строки таблиц (В КОНЕЦ) ---
    ar_i = t.counts.get(0x23, 0)
    tr_i = t.counts.get(0x01, 0)
    mr_i = t.counts.get(0x0A, 0)

    scope_sz = t._idx_size('c:ResolutionScope')
    parent_sz = t._idx_size('c:MemberRefParent')

    # ВНИМАНИЕ: RID в кодированных индексах - 1-based! Новая строка TypeRef
    # лежит под 0-based индексом tr_i, значит её RID = tr_i + 1. Без +1
    # MemberRef.resolves в TypeRef#392 (IConvertible), а не в KCDbg.
    row_ar = (_u(0, 2) * 4 + _u(0, 4) + _u(0, t.blob_idx) +
              _u(str_off[asm_name], t.str_idx) + _u(0, t.str_idx) +
              _u(0, t.blob_idx))
    row_tr = (_u(((ar_i + 1) << 2) | 2, scope_sz) +
              _u(str_off['KCDbg'], t.str_idx) + _u(0, t.str_idx))
    row_mr = (_u(((tr_i + 1) << 3) | 1, parent_sz) +
              _u(str_off['Tick'], t.str_idx) + _u(sig_idx, t.blob_idx))

    # размеры строк должны совпасть с расчётом схемы
    for tid, row, what in ((0x23, row_ar, 'AssemblyRef'),
                           (0x01, row_tr, 'TypeRef'),
                           (0x0A, row_mr, 'MemberRef')):
        expect = t.row_size(tid)
        if len(row) != expect:
            log('!! %s: собрано %d байт, схема ждёт %d' % (what, len(row), expect))
            return False

    insertions = {0x23: [(ar_i, row_ar)],
                  0x01: [(tr_i, row_tr)],
                  0x0A: [(mr_i, row_mr)]}

    token = 0x0A000000 | (mr_i + 1)          # MemberRef токен, 1-based
    log('новые строки: AssemblyRef#%d TypeRef#%d MemberRef#%d -> токен 0x%08X'
        % (ar_i, tr_i, mr_i, token))

    # --- новое тело метода ---
    new_code_body = _make_body(old_hdr, old_code, token, is_fat)
    log('новое тело: %d -> %d байт' % (len(old_hdr) + len(old_code),
                                       len(new_code_body)))

    # --- свободный хвост .text ---
    text = next((s for s in pe.sections if s.name == '.text'), None)
    if text is None:
        log('!! нет секции .text')
        return False
    slack_start = text.pr + text.vs
    slack_end = text.pr + text.rs
    if slack_start + len(new_code_body) > slack_end:
        log('!! не хватает хвоста .text: нужно %d, есть %d'
            % (len(new_code_body), slack_end - slack_start))
        return False
    # выравнивание тела до 4 байт
    body_file = (slack_start + 3) & ~3
    if body_file + len(new_code_body) > slack_end:
        body_file = slack_start
    new_rva = text.va + (body_file - text.pr)
    log('новое тело: файл 0x%x RVA 0x%x (хвост .text %d байт)'
        % (body_file, new_rva, slack_end - slack_start))

    # --- пересбор #~ ДОЛЖЕН идти ПОСЛЕ правки RVA метода, иначе в новые
    # метаданные уедет старый RVA (проверка ловила именно это) ---
    md_root = md.root

    # размещение: сначала #~, затем #Strings, в секции .reloc за EOF
    reloc = next((s for s in pe.sections if s.name == '.reloc'), None)
    if reloc is None:
        log('!! нет секции .reloc')
        return False
    file_end = len(pe.data)
    place = (file_end + 3) & ~3
    tilde_off = place

    # --- применяем изменения ---
    # 1. тело метода + RVA
    pe.data[body_file:body_file + len(new_code_body)] = new_code_body
    t.write_field_u4(6, mid, 'RVA', new_rva)

    # 2. расширение .text (VirtualSize покрывает новое тело)
    need_vs = (body_file + len(new_code_body)) - text.pr
    if need_vs > text.vs:
        struct.pack_into('<I', pe.data, text.hdr_off + 8, need_vs)
        text.vs = need_vs

    # 3. пересбор #~ (теперь RVA уже обновлён)
    new_tilde, counts = _build_tilde(t, insertions)
    strings_off = (tilde_off + len(new_tilde) + 3) & ~3
    end_file = strings_off + len(new_str)
    if new_blob is not None:
        end_file += len(new_blob) - bsize

    log('#~: 0x%x (%d байт)  #Strings: 0x%x (%d байт)'
        % (tilde_off, len(new_tilde), strings_off, len(new_str)))

    # 4. новые потоки в .reloc
    pe.data.extend(b'\0' * (place - len(pe.data)))
    pe.data[tilde_off:tilde_off + len(new_tilde)] = new_tilde
    if len(pe.data) < strings_off:
        pe.data.extend(b'\0' * (strings_off - len(pe.data)))
    pe.data[strings_off:strings_off + len(new_str)] = new_str
    if new_blob is not None:
        # куча #Blob тоже переехала - копируем целиком
        b_off = (strings_off + len(new_str) + 3) & ~3
        if len(pe.data) < b_off + len(new_blob):
            pe.data.extend(b'\0' * (b_off + len(new_blob) - len(pe.data)))
        pe.data[b_off:b_off + len(new_blob)] = new_blob
        md.set_stream('#Blob', b_off - md_root, len(new_blob))
        end_file = b_off + len(new_blob)
    if len(pe.data) < end_file:
        pe.data.extend(b'\0' * (end_file - len(pe.data)))

    # 4. заголовки потоков (#~, #Strings переехали; #US/#Blob/#GUID на месте)
    # ВАЖНО: CLI header (данные каталоги, индекс 14) НЕ трогаем - там RVA
    # самого CLI header (0x2064), а не метаданных. Поле MetaData.Size лежит
    # ВНУТРИ CLI header на смещении +12, и менять его тоже не нужно: стример
    # читает потоки по их собственным заголовкам, а промежуток между .text и
    # .reloc не является непрерывным диапазоном метаданных.
    md.set_stream('#~', tilde_off - md_root, len(new_tilde))
    md.set_stream('#Strings', strings_off - md_root, len(new_str))

    # 5. секция .reloc: наращиваем, снимаем DISCARDABLE
    new_vs = (end_file - reloc.pr)
    new_rs = end_file - reloc.pr
    align = pe.file_align
    new_rs = (new_rs + align - 1) & ~(align - 1)
    struct.pack_into('<I', pe.data, reloc.hdr_off + 8, new_vs)
    struct.pack_into('<I', pe.data, reloc.hdr_off + 16, new_rs)
    new_ch = reloc.ch & ~0x02000000              # убрать DISCARDABLE
    struct.pack_into('<I', pe.data, reloc.hdr_off + 36, new_ch)
    log('.reloc: VS %d -> %d, RS %d -> %d, ch 0x%x -> 0x%x'
        % (reloc.vs, new_vs, reloc.rs, new_rs, reloc.ch, new_ch))

    # 6. SizeOfImage
    img = (reloc.va + new_vs + pe.sect_align - 1) & ~(pe.sect_align - 1)
    if img > pe.size_of_image():
        pe.set_size_of_image(img)
        log('SizeOfImage -> 0x%x' % img)

    # --- запись (сначала во временный файл, проверяем, потом подменяем) ---
    tmp = dst + '.kcbtmp'
    with open(tmp, 'wb') as f:
        f.write(pe.data)
    if not _verify(tmp, log):
        try:
            os.remove(tmp)
        except OSError:
            pass
        log('!! файл НЕ подменён: проверка не пройдена')
        return False
    os.replace(tmp, dst)
    log('записано: %s (%d байт)' % (dst, len(pe.data)))
    return True


def _verify(path, log=print):
    """Перечитать результат и убедиться, что структура цела."""
    try:
        c = _Ctx(path)
    except Exception as e:
        log('!! проверка: не читается (%r)' % (e,))
        return False
    t = c.t
    problems = []
    # размеры потоков
    for name in ('#~', '#Strings', '#US', '#Blob', '#GUID'):
        s = c.md.stream(name)
        if s is None:
            problems.append('нет потока %s' % name)
    # каталог таблиц сходится с размером потока
    span = t.data_end - t.off
    if span > t.size:
        problems.append('таблицы не влезают в #~ (%d > %d)' % (span, t.size))
    # счётчики выросли
    if t.counts.get(0x01, 0) < 393:
        problems.append('TypeRef не добавился')
    if t.counts.get(0x0A, 0) < 2915:
        problems.append('MemberRef не добавился')
    if t.counts.get(0x23, 0) < 13:
        problems.append('AssemblyRef не добавился')
    # маркер
    if MARKER not in bytes(c.pe.data):
        problems.append('маркер KCDbg не найден')
    # тело метода
    td = c.t.find_typedef('Director')
    mid = c.t.find_method(td, 'Update') if td is not None else None
    if mid is None:
        problems.append('не найден Director.Update')
    else:
        rva = c.t.read_fields(6, mid)['RVA']
        try:
            _off, _h, code, _f = _read_body(c.pe, rva)
            if not code or code[0] != 0x28:
                problems.append('тело не начинается с call (0x28)')
            else:
                tok = struct.unpack('<I', code[1:5])[0]
                if (tok >> 24) != 0x0A:
                    problems.append('токен не MemberRef: 0x%08X' % tok)
                else:
                    log('проверка: call MemberRef#%d (0x%08X)' % (tok & 0xFFFFFF, tok))
        except Exception as e:
            problems.append('тело не читается: %r' % (e,))

    if problems:
        for p in problems:
            log('!! проверка: %s' % p)
        return False
    log('самопроверка пройдена')
    return True


# ------------------------------------------------------------- бэкап/откат

def backup_path(game_dir=None):
    return target_dll(game_dir) + '.bak'


def make_backup(game_dir=None, log=print):
    """Создать/обновить бэкап. Вернуть путь или None.

    Бэкапом считается только НЕпропатченная сборка: старый .bak, который
    лежал в папке игры, сам был пропатчен (KCDbg внутри) - он не годится
    для отката, поэтому при наличии настоящего оригинала его перезаписываем.
    """
    import shutil
    src = target_dll(game_dir)
    bak = backup_path(game_dir)
    if os.path.isfile(bak):
        src_patched = is_patched(src)
        bak_patched = is_patched(bak)
        if not (bak_patched and not src_patched):
            log('бэкап уже есть: %s' % bak)
            return bak
        log('существующий бэкап сам пропатчен - заменяю настоящим оригиналом')
    try:
        shutil.copyfile(src, bak)
    except OSError as e:
        log('!! не удалось создать бэкап: %s' % e)
        return None
    log('бэкап создан: %s' % bak)
    return bak


def unpatch(game_dir=None, log=print):
    """Вернуть исходную сборку из бэкапа."""
    import shutil
    src = target_dll(game_dir)
    bak = backup_path(game_dir)
    if not os.path.isfile(bak):
        log('!! бэкап не найден: %s' % bak)
        log('   восстановите файл через Steam: Параметры -> Локальные файлы '
            '-> Проверить целостность файлов игры')
        return False
    try:
        shutil.copyfile(bak, src)
    except OSError as e:
        log('!! не удалось восстановить: %s' % e)
        return False
    log('восстановлено из бэкапа: %s' % src)
    return True


# ------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='patcher.py',
        description='Патчинг Assembly-CSharp.dll (вставка логгера KCDbg).')
    ap.add_argument('--game-dir', default=None, help='папка игры')
    ap.add_argument('--status', action='store_true', help='проверить и выйти')
    ap.add_argument('--patch', action='store_true', help='пропатчить')
    ap.add_argument('--unpatch', action='store_true', help='восстановить бэкап')
    ap.add_argument('--no-backup', action='store_true',
                    help='не создавать бэкап (спрашивать не будем)')
    ap.add_argument('--force', action='store_true',
                    help='игнорировать вопрос (для неразговорчивых вызовов)')
    args = ap.parse_args(argv)

    game_dir = (args.game_dir or '').strip().strip('"') or gamepath.game_dir()
    if not game_dir:
        raise SystemExit('Не найдена папка игры.')

    if args.status or not (args.patch or args.unpatch):
        code, msg = status(game_dir)
        print(msg)
        return {'ok': 0, 'not_patched': 1, 'missing': 2}[code]

    if args.unpatch:
        return 0 if unpatch(game_dir) else 1

    code, msg = status(game_dir)
    if code == 'ok':
        print(msg)
        return 0
    if code == 'missing':
        print('!! %s' % msg)
        return 2

    # нужен бэкап?
    if not args.no_backup:
        if not args.force:
            ans = input('Игра не пропатчена. Создать бэкап перед патчем? [Y/n] ')
            if ans.strip().lower() in ('n', 'no', 'нет'):
                print('бэкап пропущен по запросу пользователя')
        make_backup(game_dir)

    return 0 if patch(game_dir=game_dir) else 1


if __name__ == '__main__':
    sys.exit(main())
