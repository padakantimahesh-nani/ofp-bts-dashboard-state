# V4 correction

- Panels 1, 4, and 5 permanently scope sales and stock calculations to `BACKPACK` and `KIDS FOOTWEAR`.
- Their BTS Type slicers now display only those two buttons. Clearing the slicer means both approved types, never every raw BTS Type.
- Panel 2 remains permanently scoped to `KIDS FOOTWEAR`.
- Panel 5 now keeps only rows mapped through `Code` to a Location Master record where `Type = Store`.
- Panel 5 does not use SOH `LOC Type` as a substitute for the Location Master store classification.
- Updated Streamlit width parameters to remove the warnings shown in the supplied deployment log.
