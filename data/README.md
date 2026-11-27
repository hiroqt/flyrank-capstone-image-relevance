# Corpus

Brief §3 and §7 ask for **≥40 images across ≥4 categories**, licensed-free, committed (or a download script) so evaluators can reproduce results. This folder holds **52 images across 5 subject classes plus 6 hard cases**, all committed.

## What is here
| Class | Count | Includes |
|---|---|---|
| fox | 10 | red fox in grass and snow, a kit, a young fox, at night, two arctic foxes |
| wolf | 10 | howling (×2), in snow, black wolf, yawning, carrying prey, white arctic wolf |
| dog | 10 | **Shiba Inu ×3** (fox look-alike), **husky ×4** (wolf look-alike), golden retriever ×2, Shih Tzu |
| bear | 8 | brown bears with salmon, black bears, cubs in trees |
| deer | 8 | fawns, antlered stags, deer in fog and rain, fallow deer |
| hard | 6 | a **coyote** (not in the taxonomy), a wolf silhouette at sunset, a fox in near-darkness, a motion-blurred dog, an extreme crop (deer eye and ear), a tiny fox in a car park at night. These guarantee probe P1's "≥1 flagged". |

Hard cases are marked `HARD:` in the `notes` column. Their `true_class` is still the real animal (or `coyote`).

## Rules the files follow
- **Source:** every photo is from Pexels, under the [Pexels License](https://www.pexels.com/license/): free to use and modify, attribution not required (given anyway). It forbids selling unaltered copies and redistributing on other stock-photo or wallpaper sites, which this repo does not do.
- **Size:** re-encoded by Pillow to 768 px on the long side, JPEG quality 80, metadata stripped. 3.0 MB for the whole folder.
- **Opaque filenames** such as `img_7f3a.jpg` (the first 4 hex characters of `sha256("pexels-<photo id>")`). The system must match on what the image *shows*, never its name.
- **`manifest.csv`** records each file:

```csv
file,source_url,photographer,license,true_class,notes
img_026e.jpg,https://www.pexels.com/photo/37324286/,@dmytkopl,Pexels License,bear,"black bear cub climbing a tree, dim forest"
```

`source_url` is the short Pexels link (it redirects to the photo page), so no class words reach the database. `photographer` is the Pexels username (`https://www.pexels.com/@<name>`). `true_class` and `notes` are for the eval set only: `seed` stores `source_url`, `photographer` and `license`, and the app never reads `true_class` during matching.

## How it was collected
Searched Pexels in a browser for each class (e.g. "red fox", "wolf howling", "shiba inu", "deer fog", "coyote", "wolf silhouette"), shortlisted 67 candidates, and checked every one by eye. Search results were often wrong: "wolf" returned huskies and wolfdogs, "trail camera" returned hikers, and "shiba inu" returned a figurine. Those were dropped, so every `true_class` was confirmed from the picture, not from the search term.
