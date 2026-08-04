# models/uvr — vendored stem-separation models (NOT in git)

These are binary model files (~137 MB) copied from the installed Ultimate Vocal Remover
app so cr8 does not depend on that app staying installed. They are excluded from git.

Restore them with:

```sh
M="/Applications/Ultimate Vocal Remover.app/Contents/Resources/models"
mkdir -p models/uvr
cp -n "$M/MDX_Net_Models/UVR-MDX-NET-Inst_HQ_5.onnx" models/uvr/
cp -Rn "$M/MDX_Net_Models/model_data" models/uvr/mdx_model_data
cp -n "$M/Demucs_Models/v3_v4_repo/htdemucs.yaml" \
      "$M/Demucs_Models/v3_v4_repo/955717e8-8726e21a.th" models/uvr/
```

Default recipe: `UVR-MDX-NET-Inst_HQ_5.onnx` (vocals + instrumental), `htdemucs` (drums,
bass, other). Measured ~53 s for five stems on an M1 Max. See `specs/SPEC-stems.md`.
