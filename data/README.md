# Corpus: what goes here (Phase 1 task, still to do)

Brief §3 and §7: **≥40 images across ≥4 categories**, licensed-free, committed (or a download script) so evaluators can reproduce results.

## Rules
- **Sources:** Unsplash or Pexels only. Read both license pages first.
- **Size:** resize to ≤768 px on the long side, JPEG quality ≈80 (about 60–80 KB each). Keep the whole folder under ~4 MB. Don't commit originals; `data/raw/` is gitignored.
- **Opaque filenames** such as `img_7f3a.jpg`. Never `red_fox_1.jpg`: the system must match on what the image *shows*, not its name.
- Record every image in `images/manifest.csv`:

```csv
file,source_url,photographer,license,true_class,notes
img_7f3a.jpg,https://unsplash.com/photos/...,Jane Doe,Unsplash License,fox,pouncing in snow
```

`true_class` is for the eval set only. The app never reads it during matching.

## Target mix (~50)
| Class | Count | Include |
|---|---|---|
| fox | 10 | snow pounce, kit at den, city at night, asleep, autumn leaves, arctic fox… |
| wolf | 10 | howling, pack in snow, close-up face, running… |
| dog | 10 | include a **Shiba Inu** (fox look-alike) and a **husky** (wolf look-alike) |
| bear | 8 | fishing, in a tree, cub… |
| deer | 8 | fawn, stag with antlers, in fog… |
| hard | 4–5 | blurry night camera-trap shot, distant silhouette, **coyote**, heavy crop. These guarantee probe P1's "≥1 flagged" |
