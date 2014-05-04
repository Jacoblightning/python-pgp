from pgpdump.utils import get_hex_data

from pgp import utils


# There's no real need for these to be in separate classes, it's just a
# convenient way to divide up the code without making separate modules.


class SimpleS2K(object):
    """This directly hashes the string to produce the key data."""

    mode = 0

    @classmethod
    def from_bytes(cls, symmetric_algorithm, data, offset=0):
        hash_algorithm = data[offset]
        offset += 1
        return cls(hash_algorithm, symmetric_algorithm), offset

    def __init__(self, hash_algorithm, symmetric_algorithm):
        self.hash_algorithm = hash_algorithm
        self.symmetric_algorithm = symmetric_algorithm
        self.salt = bytearray()
        self.count = 1

    def __bytes__(self):
        return bytearray([self.hash_algorithm]) + bytearray(self.salt)

    def make_hash(self, passphrase):
        required_length = utils.symmetric_cipher_block_lengths.get(
                            self.symmetric_algorithm
                        )
        hash_length = utils.hash_lengths.get(self.hash_algorithm)
        i = 0
        result = bytearray()

        while (i * hash_length) < required_length:
            # "If the hash size is less than the key size, multiple instances
            #  of the hash context are created -- enough to produce the
            #  required key data.
            hash_ = utils.get_hash_instance(self.hash_algorithm)

            # "These instances are preloaded with 0, 1, 2, ... octets of zeros
            #  (that is to say, the first instance has no preloading, the
            #  second gets preloaded with 1 octet of zero, the third is
            #  preloaded with two octets of zeros, and so forth)."
            hash_.update(bytearray([0x00] * i))
            # Spec is actually pretty vague here - had to determine when the
            # salt really should be hashed from gcrypt source.
            for i in range(self.count):
                hash_.update(self.salt)
                hash_.update(passphrase)
            # "Once the passphrase is hashed, the output data from the
            #  multiple hashes is concatenated, first hash leftmost, to
            #  produce the key data"
            result.extend(bytearray(hash_.digest()))
            i += 1

        # "any excess octets on the right [are] discarded."
        return result[:required_length]


class SaltedS2K(SimpleS2K):

    mode = 1

    @classmethod
    def from_bytes(cls, symmetric_algorithm, data, offset=0):
        hash_algorithm = data[offset]
        offset += 1
        salt = data[offset:offset + 8]
        offset += 8
        return cls(hash_algorithm, salt, symmetric_algorithm), offset

    def __init__(self, hash_algorithm, salt, symmetric_algorithm):
        SimpleS2K.__init__(self, hash_algorithm, symmetric_algorithm)
        self.salt = salt


class IteratedAndSaltedS2K(SaltedS2K):

    mode = 3

    @classmethod
    def from_bytes(cls, symmetric_algorithm, data, offset=0):
        hash_algorithm = data[offset]
        offset += 1
        salt = data[offset:offset + 8]
        offset += 8
        count = utils.s2k_count_to_int(int(data[offset]))
        offset += 1
        return cls(hash_algorithm, salt, count, symmetric_algorithm), offset

    def __init__(self, hash_algorithm, salt, count, symmetric_algorithm):
        SaltedS2K.__init__(self, hash_algorithm, salt, symmetric_algorithm)
        self.count = count

    def __bytes__(self):
        result = SaltedS2K.__bytes__(self)
        result.append(utils.int_to_s2k_count(self.count))
        return result


class GnuPGS2K(SimpleS2K):

    @classmethod
    def from_bytes(cls, symmetric_algorithm, data, offset=0):
        # GnuPG string-to-key
        # According to g10/parse-packet.c near line 1832, the 101 packet
        # type is a special GnuPG extension.  This S2K extension is
        # 6 bytes in total:
        #
        #   Octet 0:   101
        #   Octet 1:   hash algorithm
        #   Octet 2-4: "GNU"
        #   Octet 5:   mode integer
        #   Octet 6-n: serial number
        serial_number = None
        serial_len = None
        hash_algorithm = data[offset]
        offset += 1
        gnu = data[offset:offset + 3]
        offset += 3
        if gnu != bytearray(b"GNU"):
            raise ValueError(
                    "S2K parsing error: expected 'GNU', got %s" % gnu)

        mode = data[offset]
        mode += 1000
        offset += 1
        if mode == 1001:
            # GnuPG dummy
            pass
        elif mode == 1002:
            # OpenPGP card
            serial_len = data[offset]
            offset += 1
            if serial_len < 0:
                raise ValueError(
                        "Unexpected serial number length: %d" %
                        serial_len)

            serial_number = get_hex_data(data, offset,
                    serial_len)
            offset += serial_len
        else:
            raise ValueError(
                    "Unsupported GnuPG S2K extension, encountered mode %d" %
                    mode)

        return cls(hash_algorithm, mode, symmetric_algorithm,
                   serial_number, serial_len), offset

    def __init__(self, hash_algorithm, mode, symmetric_algorithm,
                 serial_number=None, serial_number_length=None):
        SimpleS2K.__init__(self, hash_algorithm, symmetric_algorithm)
        self.mode = mode
        self.serial_number = serial_number
        self.serial_number_length = serial_number_length

    def make_hash(self, passphrase):
        # TODO: complete OpenPGP card & GnuPG dummy s2k
        pass

    def __bytes__(self):
        result = bytearray(
                [self.hash_algorithm] +
                b'GNU' +
                [self.mode - 1000]
                )
        if self.mode == 1002 and self.serial_number is not None:
            result.append(self.serial_number_length)
            result.extend(utils.hex_to_bytes(self.serial_number,
                                             self.serial_number_length))
        return result


S2K_TYPES = {
    0: SimpleS2K,
    1: SaltedS2K,
    3: IteratedAndSaltedS2K,
    101: GnuPGS2K,
    }


def parse_s2k_bytes(cipher, data, offset=0):
    s2k_type = data[offset]
    offset += 1
    s2k_cls = S2K_TYPES.get(s2k_type, None)
    if s2k_cls is None:
        # TODO: bad type
        raise ValueError
    s2k, offset = s2k_cls.from_bytes(cipher, data, offset)
    return s2k, offset
