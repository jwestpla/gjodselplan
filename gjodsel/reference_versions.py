"""Fingerprints of reference CSVs shipped in earlier versions (config.reference_fingerprint: sha256 with
line endings normalised to LF, since Windows builds ship the files with CRLF).

An installed reference file that is still identical to one of these was never edited by the advisor, so a
newer bundled version may replace it (config.update_reference_files). Edited files are never overwritten.
When a file in data/reference/ changes, add the fingerprint of the version being replaced
(tests/test_config.py checks this against the git history).
"""

SHIPPED: dict[str, tuple[str, ...]] = {
    "age_factors.csv": (
        "79bf0a1adf56f97230916a92630b5aa35461a11bcd2335ced7ceeb4389e4a753",
    ),
    "cultures.csv": (
        "ee97884ab2374e507cdefb9c92e9c084cf45bbd93e2c1f0fc51810a86eee3692",
    ),
    "k_correction.csv": (
        "8fc91f22e96fb1c64229de3f0ebe01613fab3faf51009c1fa98a07f185ddc4a5",
    ),
    "n_growth_adjustment.csv": (
        "d2af14f9111664883cdb33f5b9b0441b41e2f1fea0e883c3ead56e380f7719ae",
    ),
    "n_mold_correction.csv": (
        "32cb7abb355060a730750b332ff72069ac5cdd820eb8333e10e5b06d76098362",
    ),
    "p_al_correction.csv": (
        "81a0e18a68a0f9d10a430c2d6050343e231e9442a9328425e7ae90912c6e0ca2",
    ),
    "p_limits.csv": (
        "4f3387f4b6c2f5f217822020787725b8cd08438a8da2160d6e83cdc517abeaa1",
    ),
    "products.csv": (
        "d47fd60bdc3bf6f8272e3ea7c8face06320bffa19ee1078748bde19fadfd4c75",
    ),
    "soil_optimum.csv": (
        "f086ca332e7f3fef4f3c703203e026c2ccdf9235d5fcedf5b65e33339ac7308d",
    ),
    "templates.csv": (
        "f013210bfa5b89568babc7bd992b8a5145c1287ffd9670aa90b8a628f74f0e41",
    ),
    "timings.csv": (
        "e67d202094e4e9e0053d0fec94033ba1f27e400b3841290ffa64e9792ceddd96",
    ),
}
