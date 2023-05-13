﻿import configparser
import ips_util
import os
from ds6_util import *
from tempfile import NamedTemporaryFile


class SpacePool:
    def __init__(self):
        self._available_spans = []

    @property
    def total_available_space(self):
        return sum([s['end'] - s['start'] + 1 for s in self._available_spans])

    @property
    def largest_available_space(self):
        return max([s['end'] - s['start'] + 1 for s in self._available_spans])

    def add_space(self, start, end):
        if end < start:
            raise Exception("Start must come before end!")

        should_append = True
        for span_index, span in enumerate(self._available_spans):
            if end < span['start']:
                self._available_spans.insert(span_index, { 'start': start, 'end': end} )
                should_append = False
                break
            elif start >= span['start'] and start <= span['end']:
                print(f"    Space from {start:04x} to {end:04x} overlaps with existing space from {span['start']:04x} to {span['end']:04x}")
                span['end'] = max(span['end'], end)
                should_append = False
                break
            elif start == span['end'] + 1:
                span['end'] = end
                should_append = False
                break

        if should_append:
            self._available_spans.append( { 'start': start, 'end': end } )

    def take_space(self, length, strategy='first'):
        addr = None

        best_index = None
        best_rating = None
        
        for span_index, span in enumerate(self._available_spans):
            if span['end'] - span['start'] + 1 >= length:
                if strategy == 'smallest':
                    span_rating = -(span['end'] - span['start'] + 1 - length)
                elif strategy == 'largest':
                    span_rating = span['end'] - span['start'] + 1 - length
                else:
                    span_rating = -span_index # First
        
                if best_rating is None or span_rating > best_rating:
                    best_rating = span_rating
                    best_index = span_index
        
        if best_index is not None:
            span = self._available_spans[best_index]
            addr = span['start']
            span['start'] += length
            if span['start'] > span['end']:
                del self._available_spans[best_index]
        else:
            raise Exception(f"Unable to find {length} bytes of space! Total available: {self.total_available_space} bytes; largest available: {self.largest_available_space} bytes")
   
        return addr

    def dump(self):
        print("Available space:")
        for span in self._available_spans:
            print(f"  {span['start']:04x}~{span['end']:04x} ({span['end'] - span['start'] + 1} bytes)")
        print()


def patch_data_table(patch, file_name, disk_addr, max_length, entry_stride):
    translations = load_translations_csv(file_name)
    for context, text_info in translations.items():
        if 'translation' in text_info:
            index = int(context)
            encoded = text_info['translation'].encode('shift-jis')
            if len(encoded) > max_length:
                raise Exception(f"Translation at index {index} of Data/Items is too long! original={text_info['original']}, translation={text_info['translation']} ({len(encoded)} bytes)")
            elif len(encoded) < max_length:
                encoded = encoded.rjust(max_length, b' ')
            patch.add_record(disk_addr + index*entry_stride, encoded)


def patch_menu(patch, base_addr, items, max_length, references):
    patch_data = b''
    offsets = []

    for item in items:
        offsets.append(4 + len(patch_data))
        patch_data += item.encode('shift-jis') + b'\x00'

    if len(patch_data) > max_length:
        raise Exception(f"Not enough space to patch menu at {base_addr:04x}! available={max_length} bytes; used={len(patch_data)} bytes")

    patch.add_record(base_addr - 0x4000 + 0x13e10 + 4, patch_data.ljust(max_length - 4, b'\x00'))
    
    for offset, ref_addr in zip(offsets, references):
        if ref_addr is not None:
            patch.add_record(ref_addr - 0x4000 + 0x13e10, int.to_bytes(base_addr + offset + 1, length=2, byteorder='little'))


def patch_asm(patch, nasm_path, base_addr, max_length, asm_code):
    if isinstance(asm_code, str):
        with NamedTemporaryFile(mode="w+", delete=False) as src_file, NamedTemporaryFile(mode="rb", delete=False) as dest_file:
            src_file_name = src_file.name
            dest_file_name = dest_file.name

            src_file.write("BITS 16\n")
            src_file.write(f"org 0x{base_addr:04x}\n\n")
            src_file.write(asm_code)

        if not os.path.exists(nasm_path):
            raise Exception(f"NASM is not available at the path {nasm_path}!")

        os.system(f"\"{nasm_path}\" {src_file.name} -o {dest_file_name}")

        with open(dest_file_name, "rb") as dest_file:
            encoded = dest_file.read()

        os.remove(src_file_name)
        os.remove(dest_file_name)

    else:
        encoded = asm_code

    print(f"Encoding asm patch at {base_addr:04x} ({len(encoded)}/{max_length} bytes)")

    if len(encoded) > max_length:
        raise Exception(f"Not enough space to patch asm code at {base_addr}! available={max_length} bytes; used={len(encoded)} bytes")
    
    patch.add_record(base_addr - 0x4000 + 0x13e10, encoded.ljust(max_length, b'\x90'))


def event_disk_patch_opening(event_disk_patch):
    # Opening text
    opening_trans = load_translations_csv("csv/Opening.csv")
    encoded_opening = b''
    for opening_index in range(3):
        opening_info = opening_trans[f"{opening_index+1}"]
        if 'translation' in opening_info:
            text = opening_info['translation']
        else:
            text = opening_info['original']
        
        while len(text) > 0:
            if text.startswith("<P>"):
                encoded_opening += b'\x01'
                text = text[3:]
            elif text.startswith("\n"):
                encoded_opening += b'\x00'
                text = text[1:]
            else:
                encoded_opening += text[0:1].encode('shift-jis')
                text = text[1:]
        
        if encoded_opening[-1] != 0x00:
            encoded_opening += b'\x00'
        
        if opening_index == 2:
            encoded_opening += b'\x03'
        else:
            encoded_opening += b'\x02'
        
    
    if len(encoded_opening) > 0x585:
        raise Exception(f"Opening text is too long! {len(encoded_opening)}/{0x585} bytes")
    else:
        print(f"Opening: {len(encoded_opening)}/{0x585} bytes")
        print()
    encoded_opening = encoded_opening.ljust(0x585, b'\x00')
    event_disk_patch.add_record(0x1b572, encoded_opening)


def event_disk_patch_misc(event_disk_patch):
    # Original text: 
    # プログラムディスクをドライブ１に
    # シナリオディスクを　ドライブ２に
    # セットして【RETURN】キーを
    # 押してください。
    event_disk_patch.add_record(0x1a667, b"Insert the Program Disk into\x01drive 1 and the Scenario Disk\x01into drive 2, then press the\x01\x81\x79RETURN\x81\x7a key.\x0d\x0d\x00")


def program_disk_patch_asm(program_disk_patch, nasm_path):
    # Modify the spell name formatter to use half-width digits.
    patch_asm(program_disk_patch, nasm_path, 0xa041, 0xb, '''
        mov al,0x31
        add al,[0x4183]
        stosb
        mov al,0x6
        stosb
        inc di
    ''')
    
    # Modify the first bit of spell/item text to use the passed-in SI instead of just "wa."
    patch_asm(program_disk_patch, nasm_path, 0xa798, 0x21, '''
        mov al,[0x417e]
        and al,0x3
        jz orig_a7b3
        dec al
        jz orig_a7a7
        dec al
        jz orig_a7ad

    orig_a7a7:
        call 0xa7d6
        jnc orig_a7b3
        ret

    orig_a7ad:
        push si
        call 0xa7fb
        pop si
        jnc orig_a7b3
        ret

    orig_a7b3:
        call 0x99ba
        ret
    ''')

    # Modify the second half of spell/item text to display the target as the end of a sentence where appropriate.
    patch_asm(program_disk_patch, nasm_path, 0xa7b9, 0x1d, '''
        mov si,0x57ed

        mov al,[0x417e]
        and al,0x3
        jz orig_a7d1
        mov bx,[0x40d1]
        mov al,[bx]
        cmp al,[di]
        jz orig_a7d1
        mov si,0x57e5
        
    orig_a7d1:
        and al,al
        jmp 0x8559
    ''')

    # Modify the item text to rearrange the order of output.
    patch_asm(program_disk_patch, nasm_path, 0xa1a5, 0x2a, '''
        mov si,0x57fe
        call 0xa798
        jc 0xa1da
        call 0xa7b9
        mov al,[0x419a]
        and al,0x7
        jz orig_a1c3

        mov si,0x4173
        call 0x84d7
        mov si,0x580e
        call 0x8559

    orig_a1c3:
        mov si,[0x40d1]
        call 0xa22d
        jmp 0xa1d8
    ''')

    # Modify the spell text to rearrange the order of output.
    patch_asm(program_disk_patch, nasm_path, 0xa1ed, 0x11, '''
        mov si,0x5866
        call 0xa798
        jc short 0xa22b
        mov si,0x4173
        call 0x84d7
        call 0xa7b9
    ''')

    # Modify the formatting of the save/load text.
    patch_asm(program_disk_patch, nasm_path, 0xb2a7, 0x30, '''
        push si
        mov si,0xb2de
        call 0x8559
        pop si
        call 0x8559

        cmp byte [0x82a],0xa
        jc orig_b2be

        mov si,0xb2d7
        call 0x8559
        jmp orig_b2ce

    orig_b2be:
        call 0x8559

        mov al,[0x82b]
        mov ah,0xa
        mul ah
        add al,byte [0x82a]
        inc al
        call 0x84be

    orig_b2ce:
        jmp 0x7418
    ''')

    # This is the function used to draw compressed text for location names.
    patch_asm(program_disk_patch, nasm_path, 0x893c, 0x33, '''
        push dx
        push cx
        push ax
        pushf
        
        mov cx, 0x00
    
    loop:
        lodsb
        cmp al,0x20
        jc handle_opcode
        cmp al,0x80
        jc handle_char

        jmp loop

    handle_opcode:
        sub al,0x18
        jc done
        jmp loop

    handle_char:
        mov [0x896f], cl
        call 0x8a49
        
        test cx, 1
        jz skip_stuff
        
        call 0x8b07
        inc di
        
    skip_stuff:
        inc cx
        jmp loop
        
    done:
        popf
        pop ax
        pop cx
        pop dx
        ret
    ''')
    
    # Stealing the last byte of the above routine for a local variable.
    program_disk_patch.add_record(0x896f - 0x4000 + 0x13e10, b'\x00')
    
    # This is a helper function used by the compressed text to load each
    # glyph from the font ROM.
    patch_asm(program_disk_patch, nasm_path, 0x8a49, 0x50, '''
        push di
        push dx
        push cx
        push bx
    
        mov dl, al
        mov dh, 0x09
        mov al, 00001011b
        out 01101000b, al
        mov ax, dx
        out 10100001b, al
        xchg al, ah
        out 10100011b, al
        mov di, 0x4076
        mov dl, 0x20
        mov cx, 0x10
        mov bl, 0x0
        
    row_loop:
        push cx
        mov al, dl
        out 10100101b, al
        in al, 10101001b
        mov bh, al
        mov al, dl
        mov cx, 0x8
        
    split_loop:
        rol bx, 1
        rcl ah, 1
        rol bx, 1
        rcl al, 1
        loop split_loop
        
        or al, ah
        
        test byte [0x896f], 1
        jz skip_stuff
        
        shr al, 4
        or al, [di]
    skip_stuff:
        stosb
        stosb
        inc dl
        pop cx
        loop row_loop

        pop bx
        pop cx
        pop dx
        pop di
        ret
    ''')
    
    # Bits of code used to draw the modifying descriptions for overworld
    # location names.
    patch_asm(program_disk_patch, nasm_path, 0xa11e, 0xa, '''
        mov dl, 0x57 ; W
        jnc short 0xa128
        mov dl, 0x45 ; E
        neg ax
    ''')
    patch_asm(program_disk_patch, nasm_path, 0xa133, 0xa, '''
        mov dh, 0x4e ; N
        jnc short 0xa13d
        mov dh, 0x53 ; S
        neg ax
    ''')
    patch_asm(program_disk_patch, nasm_path, 0xa164, 0x3, '''
        mov al, dh
        stosb
    ''')
    patch_asm(program_disk_patch, nasm_path, 0xa173, 0x3, '''
        mov al, dl
        stosb
    ''')

    # Clear out some unnecessary text concatenation used by the Hyper 2000/Hyper 660 item code.
    program_disk_patch.add_record(0xa73a - 0x4000 + 0x13e10, b'\x90' * 0x6)

    print()


def program_disk_patch_combat_text(program_disk_patch):
    battle_text_pool = SpacePool()
    battle_text_pool.add_space(0xbf97, 0xbfff)

    battle_text_translations = [
        { 'orig_addr': 0x41bd, 'orig_length': 0x5,  'translation': "   of <RET_IL>", 'references': [ 0x724c, 0x8317, 0xa140 ] },
        { 'orig_addr': 0x57e1, 'orig_length': 0x4,  'translation': "<X02> falls <RET_IL>", 'references': [ 0xa4b5 ] },
        { 'orig_addr': 0x57e5, 'orig_length': 0x4,  'translation': "\non <X02>.<RETN>", 'references': [ 0xa7ce ] },
        { 'orig_addr': 0x57e9, 'orig_length': 0x4,  'translation': "<X02>'s <RET_IL>", 'references': [ 0xa735 ] },
        { 'orig_addr': 0x57ed, 'orig_length': 0xd,  'translation': ".<RETN>", 'references': [ 0xa7ba ] },
        { 'orig_addr': 0x57fa, 'orig_length': 0x4,  'translation': " uses the <X0e><RET_IL>", 'references': [ 0xa1a6 ] },
        { 'orig_addr': 0x57fe, 'orig_length': 0x10, 'translation': "<X02><CALL57fa>.<RETN>", 'references': [ 0xa1d0 ] },
        { 'orig_addr': 0x580e, 'orig_length': 0xf,  'translation': " is cast!<X0a>", 'references': [ 0xa1c0 ] },
        { 'orig_addr': 0x581d, 'orig_length': 0x1a, 'translation': "'s spell was blocked!<X0a>", 'references': [ 0xa21f ] },
        { 'orig_addr': 0x584e, 'orig_length': 0x18, 'translation': "<CALL59f6><LOC5851>nothing happens.<X0a>", 'references': [ 0xa830 ] },
        { 'orig_addr': 0x5851, 'references': [ 0x9c17 ] },
        { 'orig_addr': 0x5866, 'orig_length': 0xb,  'translation': " casts <RET_IL>", 'references': [ 0xa1ee ] },
        { 'orig_addr': 0x5871, 'orig_length': 0x13, 'translation': "<X1a>Not enough MP!!<X04><X0a>", 'references': [ 0xa224 ] },
        { 'orig_addr': 0x5884, 'orig_length': 0xc,  'translation': "<X1e><RET_IL><X04> H<LOC5899>P is stolen from\n<X02>!<X0a>", 'references': [ 0xa31d ] },
        { 'orig_addr': 0x5890, 'orig_length': 0x16, 'translation': "<X1e><RET_IL><X04> M<JUMP5899>", 'references': [ 0xa35c ] },
        { 'orig_addr': 0x58a6, 'orig_length': 0xd,  'translation': "<X02><CALL0010><JUMP58cc><LOC0010>'s attack power<RETN>", 'references': [ 0xa40a ] }, # Impas (increase attack)
        { 'orig_addr': 0x58b3, 'orig_length': 0xd,  'translation': "<X02><CALL0011><JUMP58cc><LOC0011>'s defense power<RETN>", 'references': [ 0xa435 ] }, # Tuuto (increase defense)
        {                                           'translation': "<X02><CALL0011><JUMP58e0>", 'references': [ 0xa38f ] }, # Danam (decrease defense)
        { 'orig_addr': 0x58c0, 'orig_length': 0x6,  'translation': "<X02><CALL0012><JUMP58cc><LOC0012>'s speed<RETN>", 'references': [ 0xa461 ] }, # Sela (increase speed)
        {                                           'translation': "<X02><CALL0012><JUMP58e0>", 'references': [ 0xa3b7 ] }, # Hebetar (decrease speed)
        { 'orig_addr': 0x58c6, 'orig_length': 0x6,  'translation': "<X02><CALL0013><JUMP58cc><LOC0013>'s luck<RETN>", 'references': [ 0xa48d ] }, # Increase luck
        {                                           'translation': "<X02><CALL0013><JUMP58e0>", 'references': [ 0xa3df ] }, # Decrease luck
        { 'orig_addr': 0x58cc, 'orig_length': 0xb,  'translation': "increases by <RET_IL>", 'references': [ ] },
        { 'orig_addr': 0x58d7, 'orig_length': 0x9,  'translation': ".<X0a>", 'references': [ 0xa395, 0xa3bd, 0xa3e5, 0xa412, 0xa43d, 0xa467, 0xa493 ] },
        { 'orig_addr': 0x58e0, 'orig_length': 0xb,  'translation': "decreases by <RET_IL>", 'references': [ ] },
        { 'orig_addr': 0x58eb, 'orig_length': 0x10, 'translation': "<X02> recovers.<X0a>", 'references': [ 0xa545, 0xb85a ] }, # Refers to revival.
        { 'orig_addr': 0x58fb, 'orig_length': 0x1b, 'translation': "All spells are blocked.<X0a>", 'references': [ 0xa50f ] },
        { 'orig_addr': 0x5928, 'orig_length': 0x15, 'translation': "<X02> is poisoned!!<X0a>", 'references': [ 0xa4d5 ] },
        { 'orig_addr': 0x593d, 'orig_length': 0x17, 'translation': "<X02>'s spells are\nblocked!<X0a>", 'references': [ 0xa49b ] },
        { 'orig_addr': 0x5954, 'orig_length': 0xf,  'translation': "<X02> is confused!<X0a>", 'references': [ 0xa4a8 ] },
        { 'orig_addr': 0x5963, 'orig_length': 0x8,  'translation': "into a\ndeeper <LOC0005>sleep!<X0a>", 'references': [ 0xa4c4 ] },
        { 'orig_addr': 0x596b, 'orig_length': 0x12, 'translation': "a<JUMP0005>", 'references': [ 0xa4bb ] },
        { 'orig_addr': 0x597d, 'orig_length': 0xc,  'translation': "<X02> recovers <X1e><RET_IL><X04> HP!<X17>", 'references': [ 0xa88a ] },
        { 'orig_addr': 0x5989, 'orig_length': 0x17, 'translation': "<X02> recovers <X1e><RET_IL><X04> MP!<X17>", 'references': [ 0xa8ed ] },
        { 'orig_addr': 0x59a0, 'orig_length': 0x28, 'translation': "This will bring you back to\n<CALL087d>. Is that okay?<RETN>", 'references': [ 0xa634, 0xaaeb ] },
        { 'orig_addr': 0x59c8, 'orig_length': 0x7,  'translation': " attacks!<RETN>", 'references': [ 0x99ca, 0xbdf8 ] },
        { 'orig_addr': 0x59cf, 'orig_length': 0x4,  'translation': "<X02> takes <RET_IL>", 'references': [ 0x9c55 ] },
        { 'orig_addr': 0x59d3, 'orig_length': 0xe,  'translation': " damage!<X0a>", 'references': [ 0x9c5b ] },
        { 'orig_addr': 0x59e1, 'orig_length': 0x7,  'translation': "Critical hit!!<RETN>", 'references': [ 0x9c4a ] },
        { 'orig_addr': 0x59e8, 'orig_length': 0xe,  'translation': "A dire blow!!<RETN>", 'references': [ 0x9c4f ] },
        { 'orig_addr': 0x59f6, 'orig_length': 0x7,  'translation': "But <RET_IL>", 'references': [ 0x9bff ] },
        { 'orig_addr': 0x59fd, 'orig_length': 0x19, 'translation': "un<LOC5a04>fortunately, <LOC5a0a>it misses\n<X02>.<X0a>", 'references': [ 0x9c24 ] },
        { 'orig_addr': 0x5a04, 'references': [ 0x9c1c ] },
        { 'orig_addr': 0x5a0a, 'references': [ 0x9c05 ] },
        { 'orig_addr': 0x5a16, 'orig_length': 0x14, 'translation': "it has no effect.<X0a>", 'references': [ 0x9c10 ] },
        { 'orig_addr': 0x5a2a, 'orig_length': 0x12, 'translation': "?<X0a>", 'references': [ ] }, # Unused?
        { 'orig_addr': 0x5a3c, 'orig_length': 0x16, 'translation': "<X02> passes out.<X0a>", 'references': [ 0x9c76 ] },
        { 'orig_addr': 0x5a52, 'orig_length': 0xc,  'translation': "<X02> was defeated!<X0a>", 'references': [ 0x9c68 ] },
        { 'orig_addr': 0x5a5e, 'orig_length': 0x17, 'translation': "<X08><X02> gained a level!<RETN>", 'references': [ 0x9d64 ] },
        { 'orig_addr': 0x5a75, 'orig_length': 0x1d, 'translation': "Maximum HP <CALL58cc><RET_IL><LOC5a81>!<RETN>", 'references': [ 0x9d90 ] },
        { 'orig_addr': 0x5a81, 'references': [ 0x9dc5, 0x9e8c] },
        { 'orig_addr': 0x5a92, 'orig_length': 0xc,  'translation': "Maximum MP <CALL58cc><RET_IL>", 'references': [ 0x9dbf ] },
        { 'orig_addr': 0x5a9e, 'orig_length': 0x1a, 'translation': " enhancement points gained.<X0d>", 'references': [ 0x9eac ] },
        { 'orig_addr': 0x5ab8, 'orig_length': 0x8,  'translation': "Strength <JUMP58cc>", 'references': [ 0x9e61 ] },
        { 'orig_addr': 0x5ac0, 'orig_length': 0xc,  'translation': "Intellect <JUMP58cc>", 'references': [ 0x9e67 ] },
        { 'orig_addr': 0x5acc, 'orig_length': 0xc,  'translation': "Speed <JUMP58cc>", 'references': [ 0x9e6d ] },
        { 'orig_addr': 0x5ad8, 'orig_length': 0xc,  'translation': "Luck <JUMP58cc>", 'references': [ 0x9e73 ] },
        { 'orig_addr': 0x5ae4, 'orig_length': 0x2,  'translation': "<X05><RET_IL>", 'references': [ 0x9e81 ] },
        { 'orig_addr': 0x5ae6, 'orig_length': 0x4,  'translation': "Near", 'references': [ 0xa14c ] },
        { 'orig_addr': 0x5aea, 'orig_length': 0x4,  'translation': " at ", 'references': [ 0xa143 ] },
        { 'orig_addr': 0x6bb6, 'orig_length': 0x6,  'translation': "Left <X1f>", 'references': [ 0x6b87 ] }, # Needs to be exactly 6 bytes
        { 'orig_addr': 0x6bbc, 'orig_length': 0xe,  'translation': "<X1a>Unconscious  ", 'references': [ 0x6b70 ] }, # Needs to be exactly 14 bytes
        { 'orig_addr': 0x7152, 'orig_length': 0xd,  'translation': "<X0e> obtained.", 'references': [ 0x7111 ] },
        { 'orig_addr': 0x8257, 'orig_length': 0x2e, 'translation': "Strength<END>\nIntellect<END>\nSpeed<END>\nLuck<END>\nAttack<END>\nDefense", 'references': [ 0x8204 ] },
        { 'orig_addr': 0x853a, 'orig_length': 0x5,  'translation': "'s party<RET_IL>", 'references': [ 0x852f ] },
        { 'orig_addr': 0x9f1b, 'orig_length': 0x17, 'translation': "<X08>Which statistic will you enhance?<RETN>", 'references': [ 0x9ecd ] },
        { 'orig_addr': 0x9f32, 'orig_length': 0x18, 'translation': "Is this okay?<RETN>", 'references': [ 0x9efc ] },
        { 'orig_addr': 0x9f4a, 'orig_length': 0x25, 'translation': "<X1a>That statistic is already\nmaximized.<X04><X0d>", 'references': [ 0x9f14 ] },
        { 'orig_addr': 0xa745, 'orig_length': 0x14, 'translation': "<CALL0003>2000!!<X0a><LOC0003>maximum HP is now <RET_IL>", 'references': [ 0xa71b ] },
        { 'orig_addr': 0xa759, 'orig_length': 0x12, 'translation': "<CALL0003>660!!<X0a>", 'references': [ 0xa728 ] },
        { 'orig_addr': 0xaa58, 'orig_length': 0x4,  'translation': "I'll buy that <X0e> for<RETN>", 'references': [ 0xaa13 ]},
        { 'orig_addr': 0xb282, 'orig_length': 0xb,  'translation': "load\nfrom <LOC0002><RET_IL>slot <RET_IL>?<RET_IL>", 'references': [ 0xb25e ] },
        { 'orig_addr': 0xb29c, 'orig_length': 0xb,  'translation': "save\nto <JUMP0002>", 'references': [ 0xb28e ] },
        { 'orig_addr': 0xb2d7, 'orig_length': 0x7,  'translation': "RAM?<RET_IL>", 'references': [ 0xb2ba ] },
        { 'orig_addr': 0xb2de, 'orig_length': 0x17, 'translation': "Are you sure you want to <RET_IL>", 'references': [ 0xb2a9 ] },
        { 'orig_addr': 0xb3c1, 'orig_length': 0x38, 'translation': "Auto Move<END>\nLevel Up<END>\nEP Display<END>\nBGM<END>\nMovement<END>\nMessages", 'references': [ 0xb374 ] },
        { 'orig_addr': 0xb3f9, 'orig_length': 0xe,  'translation': "Off   <END>\nOn    ", 'references': [ 0xb37a, 0xb395 ] }, # Each entry in these options needs to be exactly 7 bytes
        { 'orig_addr': 0xb407, 'orig_length': 0xe,  'translation': "EP    <END>\nLeft  ", 'references': [ 0xb38c ] },
        { 'orig_addr': 0xb415, 'orig_length': 0x1c, 'translation': "Fast  <END>\nNormal<END>\nSlow  <END>\nWait  ", 'references': [ 0xb39e, 0xb3a7 ] },
        { 'orig_addr': 0xb431, 'orig_length': 0x7,  'translation': "Auto  <END>\nManual", 'references': [ 0xb383 ] },
        { 'orig_addr': 0xb51c, 'orig_length': 0x48, 'translation': " Auto battle<END>\n Auto heal<END>\n Atk spells<END>\n Heal spells<END>\n Heal items<END>\n All members", 'references': [ 0xb4a8 ] },
        { 'orig_addr': 0xb564, 'orig_length': 0xe,  'translation': "On    <END>\nOff   ", 'references': [ 0xb4b3, 0xb4c6 ] }, # These should be exactly 7 bytes?
        { 'orig_addr': 0xb572, 'orig_length': 0x10, 'translation': "Used  <END>\nNot used", 'references': [ 0xb4d9, 0xb4ec, 0xb4f2 ] }, 
        { 'orig_addr': 0xb582, 'orig_length': 0xe,  'translation': "Same  <END>\nSeparate", 'references': [ 0xb501 ] }, 
        { 'orig_addr': 0xb701, 'orig_length': 0x16, 'translation': "<X08>Victory!<RETN>", 'references': [ 0xb6dd ] },
        { 'orig_addr': 0xb717, 'orig_length': 0x11, 'translation': "<X08>The enemy dropped <X0e>.<X0a>", 'references': [ 0xb6d4 ] },
        { 'orig_addr': 0xb794, 'orig_length': 0x13, 'translation': "Gained <RET_IL> EP.<RETN>", 'references': [ 0xb75a ] },
        { 'orig_addr': 0xb7d9, 'orig_length': 0x17, 'translation': " gold obtained.<RETN>", 'references': [ 0xb7cd ] },
        { 'orig_addr': 0xb81c, 'orig_length': 0x17, 'translation': "<CH0> has lost the battle.", 'references': [ 0xb7fc ] },
        { 'orig_addr': 0xbae4, 'orig_length': 0x16, 'translation': "The poison spreads through\n<X02>.<X0a>", 'references': [ 0xbac9 ] },
        { 'orig_addr': 0xbafa, 'orig_length': 0x17, 'translation': "<X02> is going numb!!<X0a>", 'references': [ 0xbad2 ] },
        { 'orig_addr': 0xbb11, 'orig_length': 0x19, 'translation': "Poison has spread throughout\n<X02>'s body!!<X0a>", 'references': [ 0xbada ] },
        { 'orig_addr': 0xbb47, 'orig_length': 0x10, 'translation': "<X02> is sleeping.<X0a>", 'references': [ 0xbb40 ] },
        { 'orig_addr': 0xbb57, 'orig_length': 0x10, 'translation': "<X02> wakes up.<X0a>", 'references': [ 0xbb34 ] },
        { 'orig_addr': 0xbbb1, 'orig_length': 0x13, 'translation': "<JUMP5954>", 'references': [ 0xbb98 ] }, # Being confused vs becoming confused
        { 'orig_addr': 0xbbc4, 'orig_length': 0x10, 'translation': "<X02>'s head clears.", 'references': [ 0xbb8b ] },
        { 'orig_addr': 0xbe74, 'orig_length': 0x18, 'translation': "<X02> takes a defensive stance.<X0a>", 'references': [ 0xbe4f ] },
        { 'orig_addr': 0xbef3, 'orig_length': 0x10, 'translation': "<X0b> ran away.<RETN>", 'references': [ 0xbee2 ] },
    ]
    
    for battle_text_info in battle_text_translations:
        if 'orig_length' in battle_text_info:
            battle_text_pool.add_space(battle_text_info['orig_addr'], battle_text_info['orig_addr'] + battle_text_info['orig_length'] - 1)

    
    battle_text_translations.sort(key=lambda info: 0 if 'translation' not in info else len(info['translation']), reverse=True)

    battle_text_pool.dump()
            
            
    locator_addr_map = {}
    for battle_text_info in battle_text_translations:
        if 'translation' in battle_text_info:
            current_encoded, current_references, current_locators = encode_event(battle_text_info['translation'])
            addr = battle_text_pool.take_space(len(current_encoded))

            battle_text_info['encoded'] = current_encoded
            battle_text_info['internal_references'] = current_references
            battle_text_info['new_addr'] = addr

            if 'orig_addr' in battle_text_info:
                locator_addr_map[battle_text_info['orig_addr']] = addr

            for locator, offset in current_locators.items():
                locator_addr_map[locator] = addr + offset
            
    for battle_text_info in battle_text_translations:

        if 'encoded' in battle_text_info:
            if 'orig_addr' in battle_text_info:
                print(f"Relocating battle text from {battle_text_info['orig_addr']:04x} to {battle_text_info['new_addr']:04x}")
            else:
                print(f"Adding new battle text at {battle_text_info['new_addr']:04x}")
            encoded = battle_text_info['encoded']
            
            if 'internal_references' in battle_text_info:
                for internal_ref in battle_text_info['internal_references']:
                    if internal_ref[1] in locator_addr_map:
                        encoded[internal_ref[0]:internal_ref[0]+2] = int.to_bytes(locator_addr_map[internal_ref[1]], 2, 'little')

            program_disk_patch.add_record(battle_text_info['new_addr'] - 0x4000 + 0x13e10, encoded)

            for ref_addr in battle_text_info['references']:
                program_disk_patch.add_record(ref_addr - 0x4000 + 0x13e10, int.to_bytes(battle_text_info['new_addr'], 2, 'little'))

        else:
            reloc_addr = locator_addr_map[battle_text_info['orig_addr']]
            print(f"Relocating locator from {battle_text_info['orig_addr']:04x} to {reloc_addr:04x}")

            for ref_addr in battle_text_info['references']:
                program_disk_patch.add_record(ref_addr - 0x4000 + 0x13e10, int.to_bytes(reloc_addr, 2, 'little'))
    
    print(f"Remaining battle text space: {battle_text_pool.total_available_space} bytes, largest block: {battle_text_pool.largest_available_space} bytes")
    print()


def program_disk_patch_misc(program_disk_patch):
    # Miscellaneous program disk text
    program_disk_patch.add_record(0x117a3, b"  \x87\x54  The Prince's Departure  ")
    program_disk_patch.add_record(0x11940, ("Selios".encode('shift-jis') + b'\x06').ljust(0x10, b'\x00'))
    program_disk_patch.add_record(0x11980, ("Runan".encode('shift-jis') + b'\x06').ljust(0x10, b'\x00'))
    program_disk_patch.add_record(0x119c0, ("Roh".encode('shift-jis') + b'\x06').ljust(0x10, b'\x00'))
    program_disk_patch.add_record(0x11a00, ("Gail".encode('shift-jis') + b'\x06').ljust(0x10, b'\x00'))

    program_disk_patch.add_record(0xab7a - 0x4000 + 0x13e10, b"  HELL  \x00  Hard  \x00 Normal \x00  Easy  \x00")

    patch_menu(program_disk_patch, 0x7453,
        [ " Yes", " No" ], 0x11, [ None, None] )

    patch_menu(program_disk_patch, 0xa9c5,
        [ " Buy", " Sell" ], 0x1a, [ ] )

    patch_menu(program_disk_patch, 0xab1f, 
        [ " Previous town", " Restart battle", " Load a save" ], 0x3c, [ None, None, None ] )
    
    patch_menu(program_disk_patch, 0xafb7,
        [ " Spell", " Item", " Equip", " Drop", " Stats", " Other", " Leader" ], 0x36,
        [ 0xb0f4,   0xb12d,  0xb14f,   0xb182,  0xb18e,   None,     0xb1a2 ] )

    patch_menu(program_disk_patch, 0xb1e6, 
        [ " Save", " Load", " System", " Combat" ], 0x2c, 
        [ 0xb213,  0xb235,  0xb2f6,    0xb44b])

    program_disk_patch.add_record(0x1bb89, b" Fight   Spell   Guard\x01 Use     Weapon  Auto\x01 Stats   Run\x07\x00\x00\x00\x00")


def scenario_disk_patch_misc(scenario_disk_patch):
    scenario_disk_patch.add_record(0x79c66, b"Sonia\x06          ")
            
    scenario_disk_patch.add_record(0x5d438, b"  \x87\x55     The Silent Spell     ")
    scenario_disk_patch.add_record(0x88782, b"  \x87\x56    The Mark of Kings     ")
    scenario_disk_patch.add_record(0xa1770, b"  \x87\x57    The Enchanted King    ")

    # Scenario 20.00.20 (pirate minigame)
    scenario_disk_patch.add_record(0x90b63, b"\x30") # Number of wins are changed to a half-width digit.
    scenario_disk_patch.add_record(0x90b75, b"\x90") # Skip only one byte when the number of wins is 1.
    scenario_disk_patch.add_record(0x90d19, b" HP\x00 Attack\x00 Defense\x00 Speed\x00 Left\x00\x00\x00\x00\x00")


if __name__ == '__main__':
    # Setup
    configfile = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    configfile.read("ds6_patch.conf")
    config = configfile['DEFAULT']

    event_disk_patch = ips_util.Patch()
    program_disk_patch = ips_util.Patch()
    scenario_disk_patch = ips_util.Patch()


    # Build the event disk
    event_disk_patch_opening(event_disk_patch)
    event_disk_patch_misc(event_disk_patch)

    # Build the program disk
    program_disk_patch_misc(program_disk_patch)
    program_disk_patch_asm(program_disk_patch, config['NasmPath'])
    program_disk_patch_combat_text(program_disk_patch)
    patch_data_table(program_disk_patch, "csv/Items.csv", 0x1491f, 14, 20)
    patch_data_table(program_disk_patch, "csv/Spells.csv", 0x15243, 8, 11)
    patch_data_table(program_disk_patch, "csv/Locations.csv", 0x1538d, 12, 12)

    # Build the scenario disk
    scenario_disk_patch_misc(scenario_disk_patch)

    # Create patch files
    print("Creating patches...")
    print(config['OutputEventDiskPatch'])
    os.makedirs(os.path.dirname(config['OutputEventDiskPatch']), exist_ok=True)
    open(config['OutputEventDiskPatch'], 'w+b').write(event_disk_patch.encode())
    print(config['OutputProgramDiskPatch'])
    os.makedirs(os.path.dirname(config['OutputProgramDiskPatch']), exist_ok=True)
    open(config['OutputProgramDiskPatch'], 'w+b').write(program_disk_patch.encode())
    print(config['OutputScenarioDiskPatch'])
    os.makedirs(os.path.dirname(config['OutputScenarioDiskPatch']), exist_ok=True)
    open(config['OutputScenarioDiskPatch'], 'w+b').write(scenario_disk_patch.encode())
    print()

    # Apply patches to disks
    print("Patching...")

    print(f"{config['OutputEventDiskSource']} -> {config['OutputEventDisk']}")
    os.makedirs(os.path.dirname(config['OutputEventDisk']), exist_ok=True)
    with open(config['OutputEventDiskSource'], 'rb') as event_disk_in, open(config['OutputEventDisk'], 'w+b') as event_disk_out:
        event_disk_out.write(event_disk_patch.apply(event_disk_in.read()))

    print(f"{config['OutputProgramDiskSource']} -> {config['OutputProgramDisk']}")
    os.makedirs(os.path.dirname(config['OutputProgramDisk']), exist_ok=True)
    with open(config['OutputProgramDiskSource'], 'rb') as program_disk_in, open(config['OutputProgramDisk'], 'w+b') as program_disk_out:
        program_disk_out.write(program_disk_patch.apply(program_disk_in.read()))

    print(f"{config['OutputScenarioDiskSource']} -> {config['OutputScenarioDisk']}")
    os.makedirs(os.path.dirname(config['OutputScenarioDisk']), exist_ok=True)
    with open(config['OutputScenarioDiskSource'], 'rb') as scenario_disk_in, open(config['OutputScenarioDisk'], 'w+b') as scenario_disk_out:
        scenario_disk_out.write(scenario_disk_patch.apply(scenario_disk_in.read()))