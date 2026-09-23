# Appendix: understanding Vibes and audio features

A **feature** is a measured property of a recording. MusicSync turns measurements into named **buckets**, such as Warm, Bright, or Sharp, so you can choose music by how it sounds without writing numeric filters.

A **Vibe** is a saved set of these rules. It selects matching tracks from your catalog; it does not change their sound, apply an equalizer, or generate music. A name such as Studying is your intended use, not a promise that the app understands concentration or mood.

[Back to the user manual](user-guide.md) · [Using a Vibe as a circuit source](circuits.md)

## First, what does bright or warm mean?

**Tone** is the feature; **brightness** is another accepted name for it. Its scale runs:

```text
Dark  →  Warm  →  Balanced  →  Bright  →  Sharp
less high-frequency emphasis             more high-frequency emphasis
```

Think of a rounded, bass-heavy sound at one end and a more treble-heavy, sizzling sound at the other. These are listening analogies for the measurement, not guaranteed descriptions of every recording.

| Tone label | How to interpret it in MusicSync |
| --- | --- |
| Dark | The measured spectrum is weighted toward lower frequencies; the sound may feel subdued or bass-heavy. It does not mean sad or sinister. |
| Warm | On the lower-brightness side, but above Dark; often a useful starting point for rounded, less treble-forward sound. It does not identify analog equipment or recording warmth. |
| Balanced | The middle brightness bucket. It does not certify a balanced mix or equal amounts of every frequency. |
| Bright | More upper-frequency emphasis; may sound crisp or sparkling. It does not mean cheerful. |
| Sharp | The highest brightness bucket; may sound particularly treble-forward or sizzling. It does not mean the musical pitch is sharp, the recording is bad, or the sound is necessarily unpleasant. |

The analyzer averages the **spectral centroid**: roughly, the frequency center of the spectrum. Cymbals, hiss, percussion, instrumentation, and the recording's mix can all affect the result. A single label summarizes a whole recording, so a bright chorus and dark introduction can land in a middle bucket.

Use `Tone is Warm or Balanced` when you want that part of the scale. This filters existing recordings; it does not make a selected song warmer.

## Feature dictionary

The examples below explain what the measurements are trying to capture. They are not genre rules: a quiet track can be busy, a slow track can be intense, and a bright track can have clean texture.

### Tempo: how fast is the pulse?

**Labels:** Slow → Relaxed → Medium → Upbeat → Fast.

Tempo uses the app's estimated **felt tempo**, in beats per minute. It is the speed of the pulse, not the number of every small sound in a second. A slow beat can have rapid percussion on top.

MusicSync may also show detected and alternate tempo in analysis details. These acknowledge that a recording can be interpreted at half or double the pulse rate. A Tempo Vibe rule uses felt tempo. Estimation can be wrong, and re-analysis can change the result.

Example: `Tempo is Relaxed to Upbeat` includes Relaxed, Medium, and Upbeat.

### Tempo stability: how consistent is the estimated pulse?

**Labels:** Steady → Flexible → Unstable.

The analyzer looks at how much its local tempo estimates vary over the recording. Steady means lower measured variation; Flexible is intermediate; Unstable is higher variation.

This can reflect actual tempo changes, expressive timing, or difficulty tracking the beat. Unstable is not a judgment that the musicians have poor timing. A perfectly regular but ambiguous beat can confuse the estimator.

Example: `Tempo stability is Steady`.

### Activity: how many distinct attacks happen?

**Labels:** Sparse → Light → Moderate → Busy → Dense.

Activity counts detected sound onsets per second: attacks such as drum hits or note beginnings. It describes event density, rather than pulse speed. A long sustained sound can be intense but sparse; many quiet notes can be busy but light in intensity.

Example: `Activity is Moderate or less` includes Sparse, Light, and Moderate.

### Intensity: how sustained is the signal's energy?

**Labels:** Thin → Light → Moderate → Strong → Full.

Intensity uses average short-window signal energy (RMS) after the analyzer adjusts the signal to a common loudness target in memory. It is a rough descriptor of the recording's sustained energy and shape, not your speaker volume or a direct reading of emotional intensity.

Turning up playback volume does not change the saved label. Full does not mean better sound; Thin does not mean a defective recording. Analysis does not rewrite the audio file to normalize it.

Example: `Intensity is Light or Moderate`.

### Dynamics: how much do stronger moments stand above the average?

**Labels:** Flat → Steady → Balanced → Punchy → Explosive.

MusicSync compares the upper end of short-window energy with the average energy. Smaller differences produce Flat or Steady; larger differences produce Punchy or Explosive.

This is a particular energy-contrast measurement, not a complete studio assessment of dynamic range. Flat can be a consistently sustained sound, not necessarily bad mastering. Explosive can mean large contrasts, not constant loudness.

Compare Intensity with Dynamics: a consistently strong recording can have high intensity but relatively flat dynamics. A mostly quiet recording with pronounced peaks can have lower intensity and higher dynamics.

Example: `Dynamics is Punchy or more` includes Punchy and Explosive.

### Tone: where is the spectrum concentrated?

**Labels:** Dark → Warm → Balanced → Bright → Sharp.

This is the brightness scale explained above. Tone concerns frequency distribution; it is separate from loudness, emotional mood, and musical key.

Example: `Brightness is Warm` means the same thing as `Tone is Warm`.

### Texture: how tonal or noise-like is the spectrum?

**Labels:** Clean → Light → Rough → Gritty → Noisy.

Texture uses average **spectral flatness**. A spectrum concentrated into distinct frequency peaks is more tonal; energy spread more evenly across frequencies is more noise-like.

Clean therefore does not certify a clean recording, and Noisy does not prove a damaged file. Percussion, distortion, breathy sounds, and intentional noise can affect the measurement. Texture also does not count instruments.

Tone and Texture answer different questions. A bright, clear bell-like sound can be relatively tonal; a dark sound can still contain a lot of noise-like energy.

Example: `Texture is Clean-ish` accepts Clean and its neighboring Light bucket.

### Vocals: how vocal-led does the recording appear?

**Labels:** Vocal-led → Mixed vocals → Instrumental.

This uses the app's instrumentalness estimate. Treat it as a heuristic: voice-like instruments, wordless vocals, and complicated mixes can confuse it. Instrumental is not a guarantee that no voice appears anywhere. It does not identify lyrics, language, singer, or explicit content.

Example: `Vocals is Instrumental`. If it excludes too much, try `Vocals is Mixed or Instrumental` and listen to the preview results.

## Write a rule

In the Vibe editor, `A` adds a rule. The input hints show accepted features and values. You can enter a complete expression or follow the feature/value prompts. The usual form is:

```text
Feature is Value
```

These expressions are supported by the current parser:

| Expression | Meaning |
| --- | --- |
| `Tone is Warm` | One bucket |
| `Tone is Warm or Bright` | Either named bucket; Balanced is not included |
| `Tone is Warm to Bright` | An inclusive range: Warm, Balanced, Bright |
| `Tone is Warm-ish` | Warm and its immediate neighbors: Dark, Warm, Balanced |
| `Texture is Clean-ish` | Clean and Light; the scale stops at Clean |
| `Activity is Moderate or less` | Sparse, Light, Moderate |
| `Dynamics is Punchy or more` | Punchy, Explosive |
| `Tone is Warm and Activity is Light` | Adds two feature rules to the selected section/group |
| `Vocals is Mixed or Instrumental` | Mixed vocals or Instrumental |

Use **or** between accepted values for the same feature. Use **and** between complete feature clauses. This is a small rule language, not an unrestricted natural-language prompt: phrases such as “cozy songs without harsh vocals” are not supported expressions.

“More” means farther right in that feature's listed scale, not more desirable. For example, `Tempo stability is Flexible or more` includes Unstable. Prefer explicit labels when a direction feels ambiguous.

Repeated rules for the same feature in a single editor section are merged into their allowed values. To ask for Warm or Bright, write that directly rather than expecting two separate Tone rules to behave as independent constraints.

## Required Rules and groups

Every track must satisfy **all Required Rules** and **all rules in at least one normal group**. The current editor does not provide a separate all/any switch within a group.

```text
Required Rules AND (Group 1 OR Group 2 OR ...)
```

A value list such as Warm or Balanced allows either value within one rule. Groups provide alternatives across sets of features.

For a simple first Vibe, use only Required Rules:

```text
Tone is Warm or Balanced
Activity is Light or Moderate
Texture is Clean or Light
```

For alternatives, keep Tone and Texture required, then put the following in separate groups:

- Group 1: `Tempo is Relaxed or less`
- Group 2: `Activity is Light or Moderate`

This allows either the slower tracks or tracks with lighter activity, while still requiring the chosen Tone and Texture. A required Tone rule and a group Tone rule must overlap; otherwise that group cannot match.

With no normal groups, Required Rules alone apply. An empty normal group places no extra restriction, so leaving one empty can let any track satisfying Required Rules through. Preview before saving.

## Build a useful Vibe gradually

1. Download or import local audio, then analyze it using the analysis actions or `/` menu.
2. Open Collections, press `A`, and choose Vibe Collection. Use a preset or start blank.
3. Begin with one or two broad rules. A preset such as Studying, Driving, Workout, Sleep, Bright Motion, or Warm Focus is a starting recipe, not an inferred mood label.
4. Press `P` to preview matching tracks. Check songs you know to understand how the labels behave in your library.
5. Use `E` to edit a rule, `D` to delete it, or `L` to loosen it. **In this editor, L means Loosen, not Love.** Loosen includes adjacent buckets around the current allowed values.
6. Press Enter to save the Vibe from the editor. Refresh its collection when you want membership recomputed after rule or analysis changes.

If no tracks match, check that they have audio measurements, then remove or loosen one constraint at a time. Eight narrow rules can produce an empty intersection even when each one matches many tracks on its own.

The language currently selects the eight features above. Do not type status rules such as `Downloaded is true`, numeric comparisons such as `Tempo > 120`, or Harmony/Key conditions into the Vibe rule input; those are not supported by this parser. A circuit using the Vibe will separately require local audio for delivery.

## How it is measured: the mathematics

This section describes the current implementation, rather than universal definitions of musical warmth or energy. Let $x[n]$ be the decoded mono signal, $f_s$ its sample rate, and $t$ an analysis frame. The analyzer retains the file's sample rate and computes features over the recording.

### Loudness normalization before measurement

The analyzer measures integrated loudness $L$ with pyloudnorm's EBU R128 meter and scales the signal toward $L_\mathrm{target}=-14$ LUFS:

$$
y[n] = x[n]\,10^{(L_\mathrm{target}-L)/20}.
$$

This happens in memory for analysis. It does not change the saved MP3. The analyzer records whether the resulting peak would exceed 1; it does not turn this into an automatic limiter. Normalization makes the energy features less dependent on the original recording level, but it does not make all recordings have the same RMS or the same dynamics.

### Tone: the mean spectral centroid

Let $S_{k,t}$ be the magnitude of frequency bin $k$ in frame $t$, and $f_k$ its frequency in Hz. For a non-silent frame:

$$
C_t = \frac{\sum_k f_k S_{k,t}}{\sum_k S_{k,t}},
\qquad
\mathrm{Tone} = \frac{1}{T}\sum_t C_t.
$$

This is a magnitude-weighted frequency average, calculated per frame and then averaged across frames. Multiplying all magnitudes by the same gain cancels in the fraction; changing their frequency balance does not. MusicSync uses librosa's handling of silent frames. [Librosa centroid definition](https://librosa.org/doc/0.11.0/generated/librosa.feature.spectral_centroid.html)

For a toy frame with magnitude 3 at 500 Hz and magnitude 1 at 4500 Hz, the centroid is $(3\times500+1\times4500)/4=1500$ Hz. Increasing the second magnitude to 3 moves it to 2500 Hz. This illustrates the direction of brightness; a real recording has many bins and frames.

### Texture: geometric mean divided by arithmetic mean

Let $P_{k,t}=\max(S_{k,t}^2,\epsilon)$ be floored spectral power. With $K$ frequency bins:

$$
F_t = \frac{\exp\left(\frac{1}{K}\sum_k\ln P_{k,t}\right)}{\frac{1}{K}\sum_k P_{k,t}},
\qquad
\mathrm{Texture} = \frac{1}{T}\sum_t F_t.
$$

MusicSync uses librosa's defaults, including a small positive floor. Equal power in all bins gives a ratio of 1. Concentrated peaks with much smaller values elsewhere reduce the ratio. This explains why the feature measures spectral evenness rather than file quality. [Librosa spectral-feature implementation](https://librosa.org/doc/0.11.0/_modules/librosa/feature/spectral.html)

### Intensity and Dynamics: frame RMS and its distribution

For an analysis frame containing $N$ normalized samples:

$$
r_t = \sqrt{\frac{1}{N}\sum_{n\in\mathrm{frame}\ t} y[n]^2}.
$$

The stored Intensity and Dynamics values are:

$$
I = \operatorname{mean}_t(r_t),
\qquad
D = \frac{Q_{0.90}(r_t)}{I}.
$$

$Q_{0.90}$ is the 90th percentile of frame RMS values. The numerator is not the largest instantaneous sample. If mean RMS is 0.18 and the 90th percentile is 0.36, Dynamics is 2.0, which falls in Punchy. A ratio near 1 means the upper energy level is close to the average. Dynamics is undefined when the mean is nonpositive or a required value is unavailable.

### Activity: detected events per second

$$
A = \frac{N_\mathrm{detected\ onsets}}{N_\mathrm{samples}/f_s}.
$$

For example, 600 detected onsets over 200 seconds gives Activity 3/s. These are events found by the onset detector, not an exact count of played notes or instruments. This is why Activity and BPM can differ substantially.

### Tempo and Tempo stability: estimates over time

Librosa estimates a sequence of local tempos $b_1,\ldots,b_T$ from the onset-strength envelope. MusicSync stores:

$$
b_\mathrm{raw}=\operatorname{median}(b_t),
\qquad
\sigma_b=\sqrt{\frac{1}{T}\sum_t(b_t-\bar b)^2}.
$$

Tempo stability filters use $\sigma_b$. Variation is in the estimates, so detector ambiguity can increase it even if the actual performance is steady.

Felt tempo is a heuristic adjustment of $b_\mathrm{raw}$ using raw tempo, onset density, brightness, and variability. Depending on the conditions, it can use the raw value or a factor of $1/2$, $2$, $3/4$, or $3/2$. It is not simply a different unit for raw BPM. An alternate candidate may also be retained; the Vibe's Tempo rule uses the chosen felt value.

### Vocals: a heuristic score, not a probability

The analyzer first separates a harmonic component $h[n]$. From its power spectrum it computes energy ratios:

$$
v=\frac{E_{120\text{–}4000\,\mathrm{Hz}}}{E_{80\text{–}10000\,\mathrm{Hz}}},
\qquad
c=\frac{E_{300\text{–}3400\,\mathrm{Hz}}}{E_{80\text{–}10000\,\mathrm{Hz}}},
\qquad
h_r=\frac{\mathrm{RMS}(h)}{\max(\mathrm{RMS}(y),10^{-12})}.
$$

Here each band energy sums spectral power over the included bins and all frames. Define $R(z;a,b)=\operatorname{clip}((z-a)/(b-a),0,1)$. The score is:

$$
V=0.45R(c;0.35,0.70)+0.35R(v;0.50,0.85)+0.20R(h_r;0.35,0.80),
$$

$$
\mathrm{Instrumentalness}=\operatorname{clip}(1-V,0,1).
$$

These frequency bands also contain many instruments. The score is therefore a hand-designed proxy, not a speech recognizer, lyric detector, learned vocal separator, or calibrated probability that the song is instrumental. If the energy is unusable or extraction fails, the measurement is missing rather than evidence of silence or absent vocals.

### Rules as sets

Each bucket rule defines an allowed set of measured values. `Tone is Warm or Bright` is a union of two intervals. Combining distinct features in one group takes their intersection. The complete Vibe is:

$$
\mathrm{Required}\ \cap\ (\mathrm{Group}_1\ \cup\ \mathrm{Group}_2\ \cup\cdots).
$$

A missing measurement does not fall into the lowest bucket; it fails a rule that needs that feature. This also explains why adding constraints can only narrow a group's matches, while adding an alternative group can broaden the result.

## Optional numeric reference

You do not need these numbers to write a Vibe. They explain how the current **collection filters** divide measurements into the labels above. Values exactly on a boundary go into the higher bucket.

| Feature | Boundaries between consecutive labels | Measurement |
| --- | --- | --- |
| Tempo | 75, 95, 115, 140 | Felt BPM |
| Tempo stability | 8, 18 | Standard deviation of estimated local BPM |
| Activity | 1.5, 3, 5, 7 | Detected onsets per second |
| Intensity | 0.150, 0.175, 0.195, 0.215 | Mean RMS of loudness-normalized analysis signal |
| Dynamics | 1.25, 1.5, 1.8, 2.2 | 90th-percentile RMS divided by mean RMS |
| Tone | 1500, 2500, 3500, 5000 | Mean spectral centroid in Hz |
| Texture | 0.001, 0.005, 0.020, 0.080 | Mean spectral flatness |
| Vocals | 0.35, 0.75 | Instrumentalness estimate |

For example, Warm tone is a centroid from 1500 Hz up to, but not including, 2500 Hz. This does not mean the song plays a note at that pitch; it summarizes its spectrum.

One current inconsistency is worth knowing: Tempo stability labels in profile details use relative rankings among analyzed tracks, while Vibe filters use the fixed 8/18 boundaries above. A detail label and a Vibe result can therefore disagree. Use the Vibe preview to check actual membership. Analyzer changes can also alter measurements after re-analysis.

These words are MusicSync's practical vocabulary for selecting recordings. Use your ears and the preview to decide whether a recipe achieves the listening experience you want.
