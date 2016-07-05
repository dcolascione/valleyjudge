# Valleyjudge

**Valleyjudge** is a program that generates graphs that help you
decide between different tech company offers.  I wrote it to delude
myself into thinking I was deciding the rest of my life in some kind
of half-rigorous fashion.  If you have multiple offers, you may find
it useful, as I have, for convincing yourself that the facts support
when you're already decided to do.

The program takes as input details about your base salary, signing
bonus, performance bonus targets, as well as RSU grant totals and
vesting schedules, and produces graphs of cumulative after-tax income
over time.

# "Screenshot"

![Graph](/demo.png?raw=true)

# Taxes and inflation

**Valleyjudge** knows about 2016 rates for federal taxes and state
taxes in Washington state and California as well as applicable payroll
taxes on both the federal and state level; it also understands how to
compute the alternative minimum tax, and will use the AMT instead of
the federal tax under the appropriate circumstances.  I am no tax
professional, but this program computes taxes to the best of my
knowledge.  You can turn off the tax calculations if you'd rather see
before-tax figures.

There is no inflation adjustment: I'm guessing that salary, valuation,
and taxes will increase in something close enough to lockstep that
inflation doesn't affect the *relative* merits of various offers.
There's also no cost-of-living adjustment: there's no good way to
apply one automatically, and you can always apply an adjustment to the
input figures.

# User interface

**Valleyjudge** has no "user interface".  You use it by writing a
little Python program that calls into the `valleyjudge` module to
implement the main logic of the program; in this call, you give
valleyjudge your program's argv array and some arguments that describe
your offers.

(Hey, it's one step up from "configure the program by editing the
source".)

The output is a gnuplot file.  Run gnuplot on the file to generate a
pretty graph or a PDF you can turn into a poster.  The --terminal and
--output command-line options to valleyjudge control the corresponding
gnuplot parameters in the generated gnuplot file.

# Example

    $ cat demo.py 
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

    $ python3 demo.py --terminal='pngcairo font "sans,20" size 1600,1200' \
        --output='demo.png' | gnuplot \
        && open demo.png
