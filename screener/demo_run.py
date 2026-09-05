try:
    from screener.feature_engine import build_demo_df, compute_setup
except ModuleNotFoundError:  # pragma: no cover
    from feature_engine import build_demo_df, compute_setup


if __name__ == "__main__":
    df = build_demo_df()
    setup = compute_setup(df)
    print(setup)
