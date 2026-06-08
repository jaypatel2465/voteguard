from modules.security import hash_aadhar, mask_aadhar

def test_hash_aadhar_deterministic():
    aadhar = "123456789012"
    h1 = hash_aadhar(aadhar)
    h2 = hash_aadhar(aadhar)
    assert h1 == h2
    assert len(h1) == 64


def test_mask_aadhar():
    assert mask_aadhar("123456789012") == "XXXX XXXX 9012"
    assert mask_aadhar("9012") == "XXXX XXXX 9012"
    assert mask_aadhar(9012) == "XXXX XXXX 9012"
