import valleyjudge
import sys
from valleyjudge import make_offer_comparison, Offer, RsuGrant
from datetime import date

make_offer_comparison(
    title = "Example offers",
    start_date = date(2016, 8, 15),
    # taxes = None,
    show_dollars = True,
    offers = (

        Offer(
            name = "Initech",
            base = 105000,
            bonus = 50000,
            state = "CA",
            color = "red",
            grants = RsuGrant(total = 100000),
        ),

        Offer(
            name = "Contoso",
            base = 95000,
            bonus = 10000,
            state = "WA",
            color = "purple",
            grants = RsuGrant(total = 250000,
                              vesting = (0.05, 0.15, 0.40, 0.40))),

    ),
    nr_years = 4,
    argv = sys.argv)
