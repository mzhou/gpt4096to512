#!/usr/bin/python -ttu

import binascii
import collections
import os
import struct
import sys

header_fmt = '<QIIIIQQQQQQQIII'
Header = collections.namedtuple('Header', (
    'signature',
    'revision',
    'header_bytes',
    'crc32',
    'reserved',
    'current_lba',
    'backup_lba',
    'first_usable_lba',
    'last_usable_lba',
    'guid0',
    'guid1',
    'partition_table_lba',
    'num_partition_entries',
    'partition_entry_bytes',
    'partition_table_crc32',
    'filler'
))
assert struct.calcsize(header_fmt) == 92

def unpack_header(s):
    return Header(*struct.unpack(header_fmt, s[:92]), filler=s[92:])

def pack_header(header):
    return struct.pack(header_fmt, *header[:-1]) + header.filler

partition_fmt = '<QQQQQQQ'
Partition = collections.namedtuple('Partition', (
    'type_guid0',
    'type_guid1',
    'partition_guid0',
    'partition_guid1',
    'first_lba',
    'last_lba',
    'flags',
    'name'
))
assert struct.calcsize(partition_fmt) == 56

def unpack_partition(s):
    return Partition(*struct.unpack(partition_fmt, s[:56]), name=s[56:128].decode('utf-16le'))

def pack_partition(partition):
    return struct.pack(partition_fmt, *partition[:-1]) + partition.name.encode('utf-16le')

def unpack_partition_entries(s):
    assert len(s) % 128 == 0
    i = 0
    entries = []
    while i != len(s):
        entries.append(unpack_partition(s[i:i + 128]))
        i += 128
    return entries

def pack_partition_entries(partition_entries):
    return ''.join([pack_partition(partition) for partition in partition_entries])

zero_partition = Partition(0, 0, 0, 0, 0, 0, 0, u'\0' * 36)
msr_guid0, msr_guid1 = struct.unpack('<QQ', '16 E3 C9 E3 5C 0B B8 4D 81 7D F9 2D F0 02 15 AE'.replace(' ', '').decode('hex'))

def readfull(fd, pos, n):
    os.lseek(fd, pos, os.SEEK_SET)
    s = os.read(fd, n)
    if len(s) != n:
        raise Error('os.read failed (%d != %d)' % (len(s), n))
    return s

def writefull(fd, pos, s):
    os.lseek(fd, pos, os.SEEK_SET)
    written_bytes = os.write(fd, s)
    if written_bytes != len(s):
        raise Error('os.write failed (%d != %d)' % (written_bytes, len(s)))
    return written_bytes

def fd_size(fd):
    return os.lseek(fd, 0, os.SEEK_END)

def assert_eq(a, b):
    if a != b:
        raise AssertionError('%s != %s' % (repr(a), repr(b)))

def crc32(s):
    return binascii.crc32(s) & 0xffffffff

def check_header(header):
    header_nocrc = header._replace(crc32=0)._replace(filler='')
    assert_eq(struct.pack('<Q', header.signature), 'EFI PART')
    assert_eq(header.revision, 0x00010000)
    assert_eq(header.header_bytes, 92)
    assert_eq(header.crc32, crc32(pack_header(header_nocrc)))
    #if header.crc32 != crc32(pack_header(header_nocrc)):
    #    print 'CRC mismatch (%d != %d)' % (header.crc32, crc32(pack_header(header_nocrc)))
    assert_eq(header.reserved, 0)
    assert_eq(header.num_partition_entries, 128)
    assert_eq(header.partition_entry_bytes, 128)
    for c in header.filler:
        assert_eq(c, '\0')

CheckResult = collections.namedtuple('CheckResult', (
    'primary_header_lba',
    'primary_partitions_lba',
    'backup_header_lba',
    'backup_partitions_lba',
    'partitions_bytes',
    'gap_type',
    'gap_first',
    'gap_after_last',
))

def check(fd):
    gpt4096_header_s = readfull(fd, 4096, 4096)
    gpt4096_header = unpack_header(gpt4096_header_s)
    print 'Primary header: %s' % (gpt4096_header._replace(filler=''), )
    check_header(gpt4096_header)
    assert_eq(gpt4096_header.current_lba, 1)
    assert gpt4096_header.backup_lba > gpt4096_header.last_usable_lba
    partition_table_bytes = gpt4096_header.num_partition_entries * gpt4096_header.partition_entry_bytes
    partition_table_lbas = (partition_table_bytes + 4095) / 4096
    assert gpt4096_header.first_usable_lba >= gpt4096_header.partition_table_lba + partition_table_lbas
    assert gpt4096_header.last_usable_lba < gpt4096_header.backup_lba
    assert gpt4096_header.partition_table_lba > gpt4096_header.current_lba
    print 'Primary header checks out'

    gpt4096_backup_header_s = readfull(fd, 4096 * gpt4096_header.backup_lba, 4096)
    gpt4096_backup_header = unpack_header(gpt4096_backup_header_s)
    print 'Backup header: %s' % (gpt4096_backup_header._replace(filler=''), )
    check_header(gpt4096_backup_header)
    assert_eq(gpt4096_backup_header.current_lba, gpt4096_header.backup_lba)
    assert_eq(gpt4096_backup_header.backup_lba, 1)
    assert gpt4096_backup_header.first_usable_lba < gpt4096_backup_header.partition_table_lba
    assert_eq(gpt4096_backup_header.first_usable_lba, gpt4096_header.first_usable_lba)
    assert gpt4096_backup_header.last_usable_lba < gpt4096_backup_header.partition_table_lba
    assert_eq(gpt4096_backup_header.last_usable_lba, gpt4096_header.last_usable_lba)
    assert gpt4096_backup_header.partition_table_lba <= gpt4096_backup_header.current_lba - partition_table_lbas
    print 'Backup header checks out'

    entire_size_bytes = fd_size(fd)
    print 'Full disk bytes: [%d, %d)' % (0, entire_size_bytes)
    entire_size_lbas = entire_size_bytes / 4096
    print 'Full disk 4096-LBAs: [%d, %d)' % (0, entire_size_lbas)
    print 'Primary header 4096-LBAs: [%d, %d)' % (gpt4096_header.current_lba, gpt4096_header.current_lba + 1)
    assert gpt4096_header.current_lba + 1 <= gpt4096_header.partition_table_lba
    print 'Primary partition table 4096-LBAs: [%d, %d)' % (gpt4096_header.partition_table_lba, gpt4096_header.partition_table_lba + partition_table_lbas)
    first_usable_lba = gpt4096_header.first_usable_lba
    after_last_usable_lba = gpt4096_header.last_usable_lba + 1
    assert gpt4096_header.partition_table_lba + partition_table_lbas <= first_usable_lba
    print 'Usable 4096-LBAs: [%d, %d)' % (first_usable_lba, after_last_usable_lba)
    assert after_last_usable_lba <= gpt4096_backup_header.partition_table_lba
    print 'Backup partition table 4096-LBAs: [%d, %d)' % (gpt4096_backup_header.partition_table_lba, gpt4096_backup_header.partition_table_lba + partition_table_lbas)
    assert gpt4096_backup_header.partition_table_lba + partition_table_lbas <= gpt4096_backup_header.current_lba
    print 'Backup header 4096-LBAs: [%d, %d)' % (gpt4096_backup_header.current_lba, gpt4096_backup_header.current_lba + 1)
    assert gpt4096_backup_header.current_lba + 1 <= entire_size_lbas

    partitions_s = readfull(fd, 4096 * gpt4096_header.partition_table_lba, partition_table_bytes)
    partitions = unpack_partition_entries(partitions_s)
    backup_partitions_s = readfull(fd, 4096 * gpt4096_backup_header.partition_table_lba, partition_table_bytes)
    backup_partitions = unpack_partition_entries(backup_partitions_s)
    assert_eq(partitions_s, backup_partitions_s)
    assert_eq(partitions, backup_partitions)

    for partition in partitions:
        if partition == zero_partition:
            continue
        assert first_usable_lba <= partition.first_lba
        assert partition.first_lba <= partition.last_lba
        assert partition.last_lba + 1 <= after_last_usable_lba
    print 'Partition tables check out'

    print 'Looking for a 8 4096-LBA hole...'
    for a, b in [
        (gpt4096_header.current_lba + 1, gpt4096_header.partition_table_lba),
        (gpt4096_header.partition_table_lba + partition_table_lbas, first_usable_lba),
        (after_last_usable_lba, gpt4096_backup_header.partition_table_lba),
        (gpt4096_backup_header.partition_table_lba + partition_table_lbas, gpt4096_backup_header.current_lba),
        (gpt4096_backup_header.current_lba + 1, entire_size_lbas),
    ]:
        gap_size_lbas = b - a
        if gap_size_lbas >= 8:
            print 'Found gap of %d 4096-LBAs at [%d, %d)' % (gap_size_lbas, a, b)
            return CheckResult(1, gpt4096_header.partition_table_lba, gpt4096_backup_header.current_lba, gpt4096_backup_header.partition_table_lba, partition_table_bytes, 'natural', a, b)
        print 'Ignoring gap of %d 4096-LBAs at [%d, %d)' % (gap_size_lbas, a, b)

    print 'No big enough gap found between GPT metadata and usable space for 128 entry partition table'
    print 'Checking used partition extents...'
    partition_extents = []
    for partition in partitions:
        if partition != zero_partition:
            print 'Partition at 4096-LBAs: [%d, %d)' % (partition.first_lba, partition.last_lba + 1)
            if partition.type_guid0 == msr_guid0 and partition.type_guid1 == msr_guid1:
                print 'Partition is Microsoft Reserved Partition (MSR). You can shrink it to make space if it is the first partition'
            partition_extents.append((partition.first_lba, partition.last_lba + 1))
    partition_first_lba = None
    partition_after_last_lba = None
    for partition_extent in partition_extents:
        if partition_first_lba is None or partition_extent[0] < partition_first_lba:
            partition_first_lba = partition_extent[0]
        if partition_after_last_lba is None or partition_extent[1] > partition_after_last_lba:
            partition_after_last_lba = partition_extent[1]
    print 'Overall partition 4096-LBA extents: [%d, %d)' % (partition_first_lba, partition_after_last_lba)

    before_first_lbas = partition_first_lba - first_usable_lba
    if before_first_lbas >= 8:
        print 'Found gap of %d before first partition at [%d, %d)' % (before_first_lbas, first_usable_lba, partition_first_lba)
        return CheckResult(1, gpt4096_header.partition_table_lba, gpt4096_backup_header.current_lba, gpt4096_backup_header.partition_table_lba, partition_table_bytes, 'before_first', first_usable_lba, partition_first_lba)
    print 'Ignoring gap of %d 4096-LBAs before first partition at [%d, %d)' % (before_first_lbas, first_usable_lba, partition_first_lba)

    after_last_lbas = after_last_usable_lba - partition_after_last_lba
    if after_last_lbas >= 8:
        print 'Found gap of %d after last partition at [%d, %d)' % (after_last_lbas, partition_after_last_lba, after_last_usable_lba)
        return CheckResult(1, gpt4096_header.partition_table_lba, gpt4096_backup_header.current_lba, gpt4096_backup_header.partition_table_lba, partition_table_bytes, 'after_last', partition_after_last_lba, after_last_usable_lba)
    print 'Ignoring gap of %d 4096-LBAs after last partition at [%d, %d)' % (after_last_lbas, partition_after_last_lba, after_last_usable_lba)

    print 'No suitable gap found'
    return None

def ensure_gap(fd, check_result, read_only):
    if check_result.gap_type == 'natural':
        return

    gpt4096_header_s = readfull(fd, 4096 * check_result.primary_header_lba, 4096)
    gpt4096_backup_header_s = readfull(fd, 4096 * check_result.backup_header_lba, 4096)
    #gpt4096_partitions_s = readfull(fd, 4096 * check_result.primary_partitions_lba, check_result.partitions_bytes)
    #gpt4096_backup_partitions_s = readfull(fd, 4096 * check_result.backup_partitions_lba, check_result.partitions_bytes)

    gpt4096_header = unpack_header(gpt4096_header_s)
    gpt4096_backup_header = unpack_header(gpt4096_backup_header_s)

    if check_result.gap_type == 'before_first':
        new_first_usable_lba = gpt4096_header.first_usable_lba + 8
        print 'Changing first usable 4096-LBA from %d to %d' % (gpt4096_header.first_usable_lba, new_first_usable_lba)
        new_gpt4096_header = gpt4096_header._replace(crc32=0, first_usable_lba=new_first_usable_lba)
        new_gpt4096_header = new_gpt4096_header._replace(crc32=crc32(pack_header(new_gpt4096_header._replace(filler=''))))
        print 'Primary: %s' % (new_gpt4096_header._replace(filler=''), )
        new_gpt4096_backup_header = gpt4096_backup_header._replace(crc32=0, first_usable_lba=new_first_usable_lba)
        new_gpt4096_backup_header = new_gpt4096_backup_header._replace(crc32=crc32(pack_header(new_gpt4096_backup_header._replace(filler=''))))
        print 'Backup: %s' % (new_gpt4096_backup_header._replace(filler=''), )
        if not read_only:
            writefull(fd, 4096 * check_result.primary_header_lba, pack_header(new_gpt4096_header))
            writefull(fd, 4096 * check_result.backup_header_lba, pack_header(new_gpt4096_backup_header))
    elif check_result.gap_type == 'after_last':
        new_last_usable_lba = gpt4096_header.last_usable_lba - 8
        print 'Changing last usable 4096-LBA from %d to %d' % (gpt4096_header.last_usable_lba, new_last_usable_lba)
        new_gpt4096_header = gpt4096_header._replace(crc32=0, last_usable_lba=new_last_usable_lba)
        new_gpt4096_header = new_gpt4096_header._replace(crc32=crc32(pack_header(new_gpt4096_header._replace(filler=''))))
        print 'Primary: %s' % (new_gpt4096_header._replace(filler=''), )
        new_gpt4096_backup_header = gpt4096_backup_header._replace(crc32=0, last_usable_lba=new_last_usable_lba)
        new_gpt4096_backup_header = new_gpt4096_backup_header._replace(crc32=crc32(pack_header(new_gpt4096_backup_header._replace(filler=''))))
        print 'Backup: %s' % (new_gpt4096_backup_header._replace(filler=''), )
        if not read_only:
            writefull(fd, 4096 * check_result.primary_header_lba, pack_header(new_gpt4096_header))
            writefull(fd, 4096 * check_result.backup_header_lba, pack_header(new_gpt4096_backup_header))
    else:
        assert False

def inject_gpt512_partition_tables(fd, check_result, read_only):
    partitions4096_s = readfull(fd, 4096 * check_result.primary_partitions_lba, check_result.partitions_bytes)
    partitions4096 = unpack_partition_entries(partitions4096_s)

    partitions512 = []
    for partition in partitions4096:
        partitions512.append(partition._replace(first_lba=8 * partition.first_lba, last_lba=8 * (partition.last_lba + 1) - 1))
        if partitions512[-1].first_lba == 0:
            partitions512[-1] = partitions512[-1]._replace(last_lba=0)

    print '512-LBA partition table:'
    partition_extents = []
    for partition in partitions512:
        if partition != zero_partition:
            print 'Partition at 512-LBAs: [%d, %d)' % (partition.first_lba, partition.last_lba + 1)
            partition_extents.append((partition.first_lba, partition.last_lba + 1))

    print 'Writing new partition tables at %d and %d bytes' % (4096 * check_result.gap_first, 4096 * check_result.gap_first + check_result.partitions_bytes)
    if not read_only:
        writefull(fd, 4096 * check_result.gap_first, pack_partition_entries(partitions512))
        writefull(fd, 4096 * check_result.gap_first + check_result.partitions_bytes, pack_partition_entries(partitions512))

def inject_gpt512_headers(fd, check_result, read_only):
    gpt4096_header_s = readfull(fd, 4096, 4096)
    gpt4096_header = unpack_header(gpt4096_header_s)

    gpt4096_backup_header_s = readfull(fd, 4096 * gpt4096_header.backup_lba, 4096)
    gpt4096_backup_header = unpack_header(gpt4096_backup_header_s)

    partitions512_s = readfull(fd, 4096 * check_result.gap_first, check_result.partitions_bytes)
    partitions512_crc32 = crc32(partitions512_s)

    gpt512_header = gpt4096_header._replace(crc32=0, backup_lba=2, first_usable_lba=gpt4096_header.first_usable_lba * 8, last_usable_lba=8 * (gpt4096_header.last_usable_lba + 1) - 1, partition_table_lba=8 * check_result.gap_first, partition_table_crc32=partitions512_crc32, filler=('\0' * 420))
    gpt512_header = gpt512_header._replace(crc32=crc32(pack_header(gpt512_header._replace(filler=''))))
    print '512-LBA GPT primary header: %s' % (gpt512_header._replace(filler=''), )

    gpt512_backup_header = gpt4096_backup_header._replace(crc32=0, current_lba=2, backup_lba=1, first_usable_lba=gpt4096_backup_header.first_usable_lba * 8, last_usable_lba=8 * (gpt4096_backup_header.last_usable_lba + 1) - 1, partition_table_lba=8 * (check_result.gap_first + 4), partition_table_crc32=partitions512_crc32, filler=('\0' * 420))
    gpt512_backup_header = gpt512_backup_header._replace(crc32=crc32(pack_header(gpt512_backup_header._replace(filler=''))))
    print '512-LBA GPT backup header: %s' % (gpt512_backup_header._replace(filler=''), )

    if not read_only:
        writefull(fd, 512, pack_header(gpt512_header))
        writefull(fd, 1024, pack_header(gpt512_backup_header))

def main(argv):
    if len(argv) == 2:
        read_only = True
        path = argv[1]
    elif len(argv) == 3:
        if argv[1] != '-w':
            print 'Invalid argument'
            sys.exit(1)
        read_only = False
        path = argv[2]
    else:
        print 'Usage: %s [-w] <path>' % (argv[0] ,)
        print '-w: Do it for real'
        return 1

    fd = os.open(path, os.O_RDONLY if read_only else os.O_RDWR)
    try:
        check_result = check(fd)
        if check_result is None:
            print 'Exiting'
            return 2
        ensure_gap(fd, check_result, read_only)
        if check_result.gap_type == 'before_first':
            check_result = check_result._replace(gap_after_last=check_result.gap_first + 8)
        elif check_result.gap_type == 'after_last':
            check_result = check_result._replace(gap_first=check_result.gap_after_last - 8)
        inject_gpt512_partition_tables(fd, check_result, read_only)
        inject_gpt512_headers(fd, check_result, read_only)
    finally:
        os.close(fd)

    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
