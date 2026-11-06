# Five Random Test Report

## Completed automated checks

- Five focused random-number tests passed on October 20, 2026.
- TypeScript type checking completed successfully.
- Expo production web export completed successfully.
- The corrected Clear History behavior is present in the production web bundle.
- ChatGPT Sites production deployment version 2 succeeded.
- Public access is enabled at the submission URL.

## Behaviors covered

- Generates exactly five values.
- Keeps results inside the inclusive minimum and maximum boundaries.
- Produces five unique values when duplicates are disabled.
- Preserves generated order or sorts ascending as selected.
- Rejects non-whole numbers, reversed ranges, and ranges smaller than five when uniqueness is required.
- Uses rejection sampling to avoid modulo bias.
- Saves up to 20 recent sets locally.
- Clears local history after browser or native confirmation.

## Remaining limitations

- The Android APK is debug-signed for testing and is not a Google Play release package.
- iOS standalone installation requires an Apple-compatible build and signing process.
- Copy and native sharing behavior can vary with browser permissions and device capabilities.
- Historical lottery analysis is a possible future educational feature and is not included in this version.

## Integrity checks

- Android APK SHA-256: `F156A499CC6B19AA68568F515F8E85B3668D7CB70CE6CEAEBC584B998A97360D`
- Packaged web `index.html` SHA-256: `1E5F4150B373297EDF9E91C0D3B7EA5385FE80CA94C576091FF484C76DF58380`

