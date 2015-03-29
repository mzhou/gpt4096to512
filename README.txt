If you have created a GUID Partition Table (GPT) on a disk when it was
detected as having 4096 byte logical blocks by the operating system
(e.g. a Seagate 4 TB USB hard drive), and then later try to use the
disk when it is detected as having 512 byte logical blocks (e.g. when
the same drive is removed from enclosure and attached with SATA),
the GPT isn't recognised.
This is because GPT specifies that a header must appear at the 1st
(counting from 0) logical block, which would be varyingly at 4096
bytes or 512 bytes offset from the start of the disk depending on the
logical block size detected when reading it.
This tool will attempt to, given a disk formated at 4096 byte logical
blocks, inject a GPT that will be detected when the disk is accessed
with 512 byte logical blocks.

Disclaimer: May shred your data or burn your cat. Back up first!
