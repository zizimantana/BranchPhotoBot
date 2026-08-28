
2396
2397
2398
2399
2400
2401
2402
2403
2404
2405
2406
2407
2408
2409
2410
2411
2412
2413
2414
2415
2416
2417
2418
2419
2420
2421
2422
2423
2424
2425
2426
2427
2428
2429
2430
2431
2432
2433
2434
2435
2436
2437
2438
2439
2440
2441
2442
2443
2444
2445
2446
2447
2448
2449
2450
2451
2452
2453
2454
2455
2456
2457
2458
2459
2460
2461
2462
2463
2464
2465
2466
2467
2468
2469
2470
2471
2472
2473
2474
2475
2476
2477
2478
2479
2480
2481
2482
2483
import asyncio
    )


    app.router.add_post(
        "/api/submit-photo",
        api_submit_photo
    )


    return app


# =========================================================
# MAIN
# =========================================================

async def main():

    print(
        "========================================"
    )

    print(
        "Branch Photo Control Bot"
    )

    print(
        "Bot + Mini App server is starting..."
    )

    print(
        f"Web App URL: {WEBAPP_URL}"
    )

    print(
        f"Local server: http://127.0.0.1:{PORT}"
    )

    print(
        "========================================"
    )


    app = create_web_app()


    runner = web.AppRunner(
        app
    )


    await runner.setup()


    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )


    await site.start()


    try:

        await dp.start_polling(
            bot
        )


    finally:

        await runner.cleanup()

        await bot.session.close()

        db.close()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )