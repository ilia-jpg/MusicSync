# Circuits: a listening loop for an old MP3 player

A circuit keeps a small, changing queue of music on a player. You choose where songs may come from and how many to carry. MusicSync fills the player folder, lets you listen away from the computer, then replaces the songs you have heard when you reconnect.

This is designed for older MP3 players and similar devices that expose a folder over USB but do not provide useful playback history, an app, or a network connection. Instead of requiring the player to report listening events, the circuit uses something many simple players can do: **delete a file**.

[Back to the user guide](user-guide.md) · [Statuses](user-guide.md#status-reference)

## Why it is called a circuit

Music travels from your library to the player. Your listening changes the contents of the player folder. MusicSync reads those changes on your return and prepares the next queue. Information has made a round trip.

```text
Library / selected collections
            |
            v
     Fill the player folder
            |
            v
 Listen in order; delete the last track you reached
            |
            v
 Reconnect; review the detected progress marker
            |
            v
 Remove heard copies; keep pending copies; refill
            |
            +----> Listen again
```

MusicSync does not know what the player actually played. The marker is your report of progress. This works best when the player plays files in their numbered filename order. If you use shuffle or jump around, the detected marker will not accurately describe everything you heard; adjust the preview yourself or use an ordinary export instead.

## Collection, export, or circuit?

| Concept | Question it answers | Example |
| --- | --- | --- |
| Collection | Which songs belong together? | A playlist called Commute |
| Export | Which local files should I copy into this folder? | A copy of Commute on a USB stick |
| Circuit | What should stay on my player, and what should replace the songs I have heard? | Keep 25 tracks available, preserving the ones I have not reached |

A circuit can draw from multiple collections or All Downloaded. It does not download or match songs for you. Its source pool must contain local audio files. A collection with 100 titles but only 10 downloaded tracks offers at most those 10 tracks for delivery.

## Create your first circuit

Start with a small target, such as five tracks, so you can check your player's ordering before relying on a larger queue.

1. Import or download some tracks into MusicSync. Confirm they show a downloaded checkmark.
2. Connect your player and locate its drive and music folder in File Explorer.
3. Open **Circuits** with `4`, then press `A` to add one.
4. Select **All Downloaded**, or select the collections you want as sources. Use **Save and Continue** when the source selection is ready.
5. Enter a name and a target track count. This count is the number of tracks to carry, not a storage limit in megabytes.
6. Choose the destination using the path prompts. Use a dedicated folder for this circuit. If using an export root, the flow appends the circuit name beneath the chosen root/subfolder.
7. Creation makes an empty circuit. Select it and press `C` to **Circulate** for the first fill.
8. Review the preview, leave **No progress marker** selected for the first fill, and press Enter. Check **Jobs** for completion before disconnecting the player.

A circuit uses the saved destination path. If Windows gives your device a different drive letter, reconnect it at the expected location before circulating. Do not confirm circulation while the device or its folder is missing: an unavailable folder can look like every track was deleted. Verify the destination in File Explorer, not just the app's button availability.

The hidden `.musiccircuit.json` file belongs to MusicSync. Leave it in place. Do not point an ordinary managed export at the same folder: its update operation can replace MP3 files that the circuit is tracking.

## Listen, mark progress, reconnect

Play the songs in the order of their filename prefixes. When you finish a listening session, **delete the last song you reached from the player**, using its delete-file feature. This intentionally leaves a missing-file marker.

Deleting a device copy does not delete the original in MusicSync's library. It also does not tell MusicSync that you disliked the song.

If the player cannot delete files, you can still reconnect and manually choose the last song you reached in the circulation preview. You will have to remember which song that was.

### Worked example

Your player has five tracks, in order:

| Position | Song | What happened |
| --- | --- | --- |
| 1 | A | Listened |
| 2 | B | Listened |
| 3 | C | Last song reached; you delete this copy as the marker |
| 4 | D | Not reached |
| 5 | E | Not reached |

When you reconnect and press **Circulate**, MusicSync detects C as the marker. Confirming it means **everything through C counts as heard**.

- A and B are removed from the player folder. C is already missing.
- D and E stay on the player with their existing filenames.
- Up to three eligible replacement tracks are appended to return the queue to five.
- A, B, and C remain in your computer's library and can return on a later circulation. They are excluded from the refill that immediately replaces them.

The preview may say **Heard: 2**, **Missing markers: 1**, **Fresh: 2/5**, and **Add: 3 surprises**. In this view, Heard counts present files that will be removed; the already-deleted C is counted separately. Fresh means pending tracks left in the queue, not songs you have never heard in your life.

If several files are missing, MusicSync uses the **furthest missing track in the queue** as the detected marker. An accidental deletion near the end could therefore imply more progress than intended. Always review the marker before confirming.

## Understand the circulation preview

The preview shows the previous queue and a **No progress marker** row. The detected marker remains indicated even if you move the selection.

**The row selected when you press Enter becomes the last-heard marker.** Moving the cursor changes the proposed cut: tracks through that row are considered heard; tracks after it are kept if still present. Enter applies the circulation, rather than merely opening the selected track.

This matters when you move around to love or boo songs. After editing feedback, return the selection to the correct last-heard song before pressing Enter.

Choose **No progress marker** when you do not want any remaining files removed for listening progress. MusicSync can still top up a short queue. Missing files do not reappear merely because you select this row; the refill chooses eligible tracks.

The preview shows how many tracks will stay, be removed, or be added. Refill titles are intentionally hidden until delivery, keeping the next songs a surprise. Press Esc to cancel the preview, including unsaved feedback changes made there.

## Love, boo, and neutral

Feedback tells MusicSync what you want to hear again. It is separate from the listening marker.

| Key / symbol | Meaning |
| --- | --- |
| `L` / ♥ | Love this song in the selected feedback scope |
| `B` / ✕ | Boo this song in the selected feedback scope |
| `G` | Switch between Global and Here on circuit feedback screens |
| · | Neutral: no current love or boo in that scope |

Press the same rating key again to return that scope to neutral. Choosing the opposite rating replaces the previous one.

### Global versus Here

**Global** means your general preference for the song across circuits. **Here** means your preference for this one circuit. Both can exist at the same time.

For example, you might globally love a quiet piano track but boo it **Here** in a running circuit. You still like the track; it is unsuitable for that listening context.

Under the current default refill policy:

| Preference | Effect on future refill selection |
| --- | --- |
| Global love | Makes the track more likely |
| Global boo | Makes the track less likely, but does not ban it |
| Here love | Gives an additional preference boost in this circuit |
| Here boo | Excludes the track from new refills in this circuit, even if globally loved |
| Neutral | No rating adjustment in that scope |

A love is not a promise that the next refill will contain the song. A boo does not delete audio or immediately remove a pending copy already on the player. These preferences affect selection of new refill tracks.

Ordinary Media and collection track lists use `L` and `B` for **Global** feedback. Circuit track feedback screens offer both columns and save changes directly. Feedback edited in the circulation preview is saved only when you confirm circulation; Esc discards those preview edits.

## How the next songs are chosen

Refill is a weighted random selection from eligible downloaded tracks:

- Songs already kept in the queue are excluded.
- Songs just marked heard are excluded from that circulation's refill.
- The same song in multiple selected collections is counted once, rather than getting extra chances.
- Songs never sent through this circuit get a preference boost.
- Recently sent or heard songs receive a penalty; older songs gradually become more competitive again.
- Global and Here feedback adjust selection as described above.
- A track is chosen at most once per refill.

This balances variety with preference. It is not a strict oldest-first queue, a permanent no-repeat system, or a guarantee that every eligible track will appear before any repeat.

If too few eligible files remain, the circuit fills as far as it can. It does not download missing songs or ignore Here boos to meet the target. Add more downloaded tracks to the source pool, broaden the selected collections, or reconsider the target count.

## Why the filenames look unusual

Circuit filenames have sortable prefixes such as `001`, `009`, `00A`, `00Z`, and `010`. Letters extend the numbering scheme while keeping prefixes short.

New files are appended using later positions. Kept files are not renamed or recopied during ordinary circulation. This preserves queue continuity and avoids unnecessary writes to the player's flash storage. Gaps in the sequence are normal.

Some older players sort by tags or filesystem order rather than filenames. Test yours with a small circuit. The progression rule assumes the actual listening order agrees with the circuit order; MusicSync cannot enforce the player's playback behavior.

## Editing, deleting, and common questions

**Changing the source or target:** Use `E` on the circuit. Source changes affect future refill candidates; existing pending tracks are kept. Lowering the target does not immediately trim a longer pending queue. Circulation adds tracks only when the kept count is below the target.

**Deleting a circuit:** The current Delete action archives the circuit in MusicSync. It does not erase the player folder or your library audio.

**I want every song replaced:** Choose the last track as the progress marker and review the counts before confirming. The old queue is excluded from that immediate refill, so a small source pool can leave the player underfilled.

**I want the same fixed album or playlist:** Use an export. A circuit is useful when you want a continuing listening queue with changing contents.

**The circuit is In Loop but the player is not connected:** In Loop is a folder/manifest and previous-delivery state, not live playback telemetry. Confirm the actual device path before circulating.

**Can this work on a USB stick or SD card?** The app operates on folders, so the storage medium is not special. The useful player features are predictable ordering and, for automatic marker detection, an intentional way to delete the last song reached.
