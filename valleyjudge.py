#!/usr/bin/python3

import sys
from collections import namedtuple
from datetime import date, datetime, timedelta
from collections import defaultdict, Iterable
import numbers
from types import new_class
import shlex
from itertools import accumulate
import functools
from argparse import ArgumentParser
from os.path import basename

import logging
log = logging.getLogger(__name__)

def typecheck(value, type):
    """Throw if VALUE is not is-a TYPE"""
    if not isinstance(value, type):
        raise TypeError(value, type)
    return value

def seq_of(element_type, min_elements = None, max_elements = None):
    """Generate a type for typecheck that checks for a sequence of a type."""
    class PredicateMeta(type):
        def __instancecheck__(self, instance):
            if not isinstance(instance, Iterable):
                return False
            if min_elements is not None or max_elements is not None:
                instance_len = len(instance)
                if min_elements is not None and instance_len < min_elements:
                    return False
                if max_elements is not None and instance_len > max_elements:
                    return False
            return all(isinstance(x, element_type) for x in instance)
    return PredicateMeta("seq_of(%r)" % (element_type,), (object,), {})

def pair_of(element_type):
    return seq_of(element_type, 2, 2)

inf = float('inf')

DEFAULT_PAYDAYS = (1, 15)
DEFAULT_NR_YEARS = 4
DEFAULT_VESTING_DATES = ((2, 20), (5, 20), (8, 20), (11, 20))
DEFAULT_SERIES = ("cash", "total")
DEFAULT_SERIES_STYLES = {
    "cash": ("dashtype", '"______"'),
    "tax": ("dashtype", '". _ _ . _ _ ."'),
}
AUTO_COLORS = (
    "red",
    "green",
    "blue",
    "purple",
    "yellow",
    "brown",
)

DEFAULT_BONUS_DATES = ((1,1), (6, 1))
DEFAULT_REFESHER_DATES = ((1, 1))

DEFAULT_TERMINAL = 'wxt font "times,20" size 2000,1000'

NO_TAXES = (
    (0, inf),
)

FEDERAL_TAX_BRACKETS_2016 = (
    (0.100, 9275),
    (0.150, 37650),
    (0.250, 91150),
    (0.280, 190150),
    (0.330, 413350),
    (0.350, 415050),
    (0.396, inf),
)

PERSONAL_EXEMPTION_2016 = (4050, 259400, 381900)

AMT_EXEMPTION_2016 = (53900, 119700, 333600)
AMT_BRACKETS_2016 = (
    (.260, 186300),
    (.280, inf),
)

STATE_TAX_BRACKETS_2016 = {
    "CA": (
        (0.010, 7582),
        (0.020, 17976),
        (0.040, 28371),
        (0.060, 39384),
        (0.080, 49774),
        (0.093, 254250),
        (0.103, 305100),
        (0.113, 508500),
        (0.123, 1000000),
        (0.133, inf), # Mental Health Services Tax: +1% > $1e6
    ),
    "WA": NO_TAXES,
}

STATE_SSDI_BRACKETS_2016 = {
    "CA": (
        (0.009, 106742),
        (0, inf), # sic
    ),
    "WA": NO_TAXES,
}

MEDICARE_TAX_BRACKETS_2016 = (
    (0.0145, inf),
)

SOCIAL_SECURITY_TAX_BRACKETS_2016 = (
    (0.062, 118500),
    (0, inf), # sic
)

FEDERAL_STANDARD_DEDUCTION_2016 = 6300
STATE_STANDARD_DEDUCTIONS_2016 = {
    "CA": 4044,
    "WA": 0,
}

class Taxes(object):
    """Calculate income taxation"""
    def __init__(self,
                 *,
                 personal_exemption,
                 amt_exemption,
                 amt_brackets,
                 federal_standard_deduction,
                 federal_brackets,
                 medicare_brackets,
                 social_security_brackets,
                 state_standard_deductions,
                 state_brackets,
                 state_ssdi_brackets,
                 income_events):
        """Calculate tax liability.  Amateur code; probably incorrect.
        Assumes single taxpayer of high enough income to avoid
        weirdness like EITC.

        PERSONAL_EXEMPTION is a tuple of three elements: (AMOUNT,
        PHASE_OUT_BEGIN, and PHASE_OUT_END).  AMT_EXEMPTION is a
        similar phaseout tuple.

        AMT_EXEMPTION and AMT_BRACKETS are used for computing the
        alternative minimum tax, which is used in place of the
        standard federal tax if it comes out to a larger sum.

        FEDERAL_STANDARD_DEDUCTION is the federal standard deduction;
        STATE_STANDARD_DEDUCTION is analogous.

        FEDERAL_BRACKETS is sequences of (RATE, LIMIT) pairs; the last
        LIMIT should be infinity.  These sequences represent the
        various brackets of any progressive (or regressive, since RATE
        does not need to increase) tax scheme.

        MEDICARE_BRACKETS and SOCIAL_SECURITY_BRACKETS are similar
        data structures for medicare and social security
        rates, respectively.

        STATE_STANDARD_DEDUCTIONS, STATE_BRACKETS, and
        STATE_SSDI_BRACKETS are dictionaries mapping each state
        abbreviation to structures analogous to the similarly-named
        federal concepts.  The Taxes class independently tracks income
        earned in each state.

        INCOME_EVENTS is a sequence of (STATE, DATE, AMOUNT) tuples;
        these tuples represent income of any sort.  We need to know
        total income because the effective tax rate depends on total
        income for a calendar year.  """
        income_by_year = defaultdict(functools.partial(defaultdict, int))
        self.__income_dates = set()
        for date, amount, state in income_events:
            income_by_year[date.year][state] += amount
            self.__income_dates.add(date)
        self.__income_by_year = income_by_year
        self.__tax_by_year = {}
        for year in sorted(income_by_year):
            iby = income_by_year[year]
            income = sum(iby.values())
            total_state_tax = 0
            for state, state_income in sorted(iby.items()):
                sb = state_brackets[state]
                ssd = state_standard_deductions[state]
                state_agi = max(0, state_income - ssd)
                state_tax = Taxes.calculate_due(state_agi, sb)
                state_sdi = Taxes.calculate_due(
                    state_income,
                    state_ssdi_brackets[state])
                log.debug(("year:%r state:%r state_income:%g "
                           "state_agi:%g state_tax:%g state_sdi:%g "
                           "rate:%g%%"),
                          year, state, state_income, state_agi, state_tax,
                          state_sdi,
                          100.0*((state_tax + state_sdi) / state_income))
                total_state_tax += state_tax + state_sdi

            pe_phaseout = ((income - personal_exemption[1]) /
                           (personal_exemption[2] - personal_exemption[1]))
            pe_phaseout = min(max(0, pe_phaseout), 1)
            effective_pe = personal_exemption[0] * (1 - pe_phaseout)

            federal_agi = max(0, income - effective_pe)
            federal_itemized_deduction = total_state_tax
            federal_deduction = max(federal_itemized_deduction,
                                    federal_standard_deduction)
            federal_tax = Taxes.calculate_due(
                max(0, federal_agi - federal_deduction),
                federal_brackets)
            amti = income
            amte_phaseout = ((amti - amt_exemption[1]) /
                             (amt_exemption[2] - amt_exemption[1]))
            amte_phaseout = min(max(0, amte_phaseout), 1)
            effective_amte = amt_exemption[0] * (1 - amte_phaseout)
            amt_base = max(0, amti - effective_amte)
            amt = Taxes.calculate_due(amt_base, amt_brackets)

            if federal_tax < amt:
                log.info("AMT!!! year:%s income:%g fedtax:%g amtax:%g",
                         year, income, federal_tax, amt)
            total_tax = max(federal_tax, amt) + state_tax
            self.__tax_by_year[year] = federal_tax + state_tax

            medicare_tax = Taxes.calculate_due(
                income, medicare_brackets)
            total_tax += medicare_tax

            social_security_tax = Taxes.calculate_due(
                income, social_security_brackets)
            total_tax += social_security_tax

            log.debug(("year:%r income:%g effpe:%g totded:%g fedagi:%g "
                       "total_tax:%g rate:%g%%"),
                      year,
                      income,
                      effective_pe,
                      federal_deduction,
                      federal_agi,
                      total_tax,
                      100.0 * (total_tax / income))

    @staticmethod
    def calculate_due(gross_pay, brackets):
        total_tax = 0
        for rate, limit in brackets:
            tax_basis = min(limit, gross_pay)
            total_tax += tax_basis * rate
            gross_pay -= tax_basis
        return total_tax

    def calculate_take_home_pay(self, date, raw_pay):
        """Return take-home pay on a date given raw pay."""
        assert date in self.__income_dates, date
        year_income = sum(self.__income_by_year[date.year].values())
        year_tax = self.__tax_by_year[date.year]
        taxed_pay = raw_pay - (raw_pay / year_income) * year_tax
        return taxed_pay

class Taxes2016(Taxes):
    """Taxes class for 2016"""
    def __init__(self, income_events):
        Taxes.__init__(
            self,
            personal_exemption = PERSONAL_EXEMPTION_2016,
            amt_exemption = AMT_EXEMPTION_2016,
            amt_brackets = AMT_BRACKETS_2016,
            federal_standard_deduction = FEDERAL_STANDARD_DEDUCTION_2016,
            federal_brackets = FEDERAL_TAX_BRACKETS_2016,
            medicare_brackets = MEDICARE_TAX_BRACKETS_2016,
            social_security_brackets = SOCIAL_SECURITY_TAX_BRACKETS_2016,
            state_standard_deductions = STATE_STANDARD_DEDUCTIONS_2016,
            state_brackets = STATE_TAX_BRACKETS_2016,
            state_ssdi_brackets = STATE_SSDI_BRACKETS_2016,
            income_events = income_events)

class RsuGrant(object):
    """Equity grant"""
    def __init__(self,
                 *,
                 total,
                 start = None,
                 vesting_dates = DEFAULT_VESTING_DATES,
                 vesting = (0.25, 0.25, 0.25, 0.25)):
        """Create an equity grant description.

        TOTAL is the total size, in dollars, of the grant.  START is
        the date on which it starts; if None, the grant clock starts
        on the company start date.  VESTING_DATES is a sequence of
        (MONTH, DAY) pairs on which equity grants vest --- a grant
        that vests quarterly will have a four-element
        VESTING_DATES sequence.

        VESTING is a sequence of numbers that sum to 1.0.  Each one
        represents a year over which the grant vests, and the value of
        the number indicates the portion of the grant that vests in
        that year.

        """
        self.total = typecheck(total, numbers.Real)
        self.start = typecheck(start, (date, timedelta, type(None)))
        self.vesting_dates = typecheck(vesting_dates, seq_of(pair_of(int)))
        self.vesting = typecheck(vesting, seq_of(numbers.Real))
        if sum(vesting) != 1.0:
            raise ValueError("vesting fractions do not sum to 1")

class Offer(object):
    """Describes an offer"""
    def __init__(self, *,
                 name,
                 base,
                 state,
                 bonus = 0,
                 color = None,
                 bonus_target = 0,
                 bonus_dates = DEFAULT_BONUS_DATES,
                 refresher_amount = 0,
                 refresher_dates = DEFAULT_REFESHER_DATES,
                 grants = ()):
        """Object representing an offer.

        NAME is a string giving the name of the offer, usually
        something to do with the company you'll be working at.
        BASE is a number giving the base salary offered; STATE is a
        two-letter state abbreviation that indicates where you'll be
        getting paid.  BONUS is a number of dollars you'll receive
        up-front when you accept the offer.  COLOR is a string giving
        a color (name or six-digit hex) we use for the company in the
        group.

        BONUS_TARGET is a non-negative number that, when multiplied by
        BASE, gives the annual performance bonus target.  BONUS_DATES
        is a sequence of (MONTH, DAY) pairs describing when the
        performance bonus is distributed.

        REFRESHER_AMOUNT is the number of dollars distributed at each
        equity refresher issuing; REFRESHER_DATES is a sequence of
        (MONTH, DAY) pairs indicating when refresher bonuses are given
        out.  REFRESHER_AMOUNT and REFRESHER_DATES just automatically
        create RsuGrant objects; you can instead manually list
        expected refresher grants if you want more control.

        GRANTS is a sequence of RsuGrant objects; see the help for the
        RsuGrant class.

        """
        self.name = typecheck(name, str)
        self.base = typecheck(base, numbers.Real)
        self.bonus = typecheck(bonus, numbers.Real)
        if isinstance(grants, RsuGrant):
            grants = (grants,)
        self.grants = typecheck(grants, seq_of(RsuGrant))
        self.state = typecheck(state, str)
        self.color = typecheck(color, (str, type(None)))
        self.bonus_target = typecheck(bonus_target, numbers.Real)
        self.bonus_dates = typecheck(bonus_dates, seq_of(pair_of(int)))
        self.refresher_amount = typecheck(refresher_amount, numbers.Real)
        self.refresher_dates = typecheck(bonus_dates, seq_of(pair_of(int)))

def iterdates(start, end):
    return (start + timedelta(n) for n in range(0, (end - start).days))

def pay_info(offer,
             day,
             start_date,
             paydays,
             vests):
    """Determine pay on a given day.

    OFFER is an Offer object; DAY is a datetime.date.  PAYDAYS is a
    tuple of day-numbers-of-month on which normal cash pay is
    received; VESTS is a sequence of vesting events as generated by
    the make_vests function.

    Return a tuple of (CASH, EQUITY), either or both of which may
    be zero."""

    cash = 0
    equity = 0
    if day == start_date:
        cash += offer.bonus
    elif day.day in paydays:
        cash += offer.base / (12*len(paydays))
    if (day.month, day.day) in offer.bonus_dates:
        nbonus = len(offer.bonus_dates)
        bonus_period = 365 / nbonus
        bonus_amount = (offer.bonus_target * offer.base) \
                       / len(offer.bonus_dates)
        bonus_amount *= min(1.0, (day - start_date).days / bonus_period)
        cash += bonus_amount

    for voffer, vdate, vamount in vests:
        if voffer is offer and vdate == day:
            equity += vamount
    return (cash, equity)

def gen_raw_pay(offer,
                start_date,
                end_date,
                paydays,
                vests):
    """Generate pre-tax income events.

    Inputs are as for the pay_info function.

    Each income event is a tuple of (DAY, CASH, and EQUITY).  DAY is a
    datetime.date object giving the day of income dispersal; CASH and
    EQUITY (either or both of which can be zero) is the amount of
    money earned on that day."""
    for day in iterdates(start_date, end_date):
        cash, equity = pay_info(offer, day, start_date, paydays, vests)
        yield (day, cash, equity)

def make_vests(
        total,
        annual_ratios,
        start_date,
        vesting_dates):
    """Generate a vesting schedule for a grant.

    TOTAL is the total value, in dollars, of the grant.  ANNUAL_RATIOS
    is a sequence of numbers, which must sum to one, that determine
    how much of the grant vests in each year.  START_DATE is a
    datetime.date object indicating when the grant clock starts
    ticking.  VESTING_DATES is a sequence of (MONTH, DAY) tuples that
    indicate the months and days when grants vest each year.

    Return a sequence of (VDATE, VAMOUNT) tuples; each VDATE is a date
    on which a vest happens; and VAMOUNT is the amount, in dollars,
    given out on that day.

    """
    vests = []
    cliff_vest_day = start_date.replace(year = start_date.year + 1)
    vests.append((cliff_vest_day, annual_ratios[0] * total))
    annual_ratios = annual_ratios[1:]
    nrv = 0
    d = cliff_vest_day + timedelta(days=1)
    while annual_ratios:
        if (d.month, d.day) in vesting_dates:
            dist_from_cliff = d - cliff_vest_day
            frac = annual_ratios[0] / len(vesting_dates)
            vests.append((d, frac * total))
            nrv += 1
        if nrv == len(vesting_dates):
            nrv = 0
            annual_ratios = annual_ratios[1:]
        d += timedelta(days=1)
    return vests

def gnuplot_quote(s):
    s = shlex.quote(s)
    if not s or s[0] not in ('"', "'"):
        s = '"' + s + '"'
    return s

def add_rows_pairwise(row1, row2):
    rlen = len(row1)
    assert rlen == len(row2)
    return tuple([row2[0]] + [row1[i] + row2[i] for i in range(1, rlen)])

def make_earnings_table(
        offers,
        start_date,
        nr_years,
        taxes,
        already_earned_first_year,
        already_earned_state,
        paydays):
    end_date = start_date.replace(
        year = start_date.year + nr_years,
    )
    end_date += timedelta(days=1)
    vests = []
    for offer in offers:
        offer_vests = []
        offer_grants = offer.grants[:]
        if offer.refresher_amount:
            if not offer_grants:
                raise ValueError("refresher specified with no initial grant")
            for day in iterdates(start_date, end_date):
                if (day.month, day.day) in offer.refresher_dates:
                    offer_vests.extend(
                        make_vests(
                            offer.refresher_amount,
                            offer_grants[0].vesting,
                            day,
                            offer_grants[0].vesting_dates))
        for grant in offer_grants:
            grant_start = grant.start
            if grant_start is None:
                grant_start = start_date
            elif isinstance(grant_start, timedelta):
                grant_start = start_date + grant_start
            else:
                assert isinstance(grant_start, date)
            if grant_start < start_date:
                raise ValueError("grant starts before job start",
                                 offer, grant_start, start_date)
            offer_vests.extend(
                make_vests(
                    grant.total,
                    grant.vesting,
                    grant_start,
                    grant.vesting_dates))
        vests.extend(tuple([offer] + list(x) for x in offer_vests))

    if taxes:
        tax_end_date = end_date.replace(year = end_date.year + 1)
        offer_taxes = tuple(
            taxes(
                [(start_date,
                  already_earned_first_year,
                  already_earned_state or offer.state)] +
                [(day, cash + equity, offer.state)
                 for day, cash, equity
                 in gen_raw_pay(
                     offer,
                     start_date,
                     tax_end_date,
                     paydays,
                     vests)])
            for offer in offers)

    data = []
    for day in iterdates(start_date, end_date):
        fields = [day]
        for i, offer in enumerate(offers):
            cash, equity = pay_info(
                offer,
                day,
                start_date,
                paydays,
                vests)
            tax = 0
            if taxes and (cash > 0 or equity > 0):
                taxes = offer_taxes[i]
                taxed_cash = taxes.calculate_take_home_pay(day, cash)
                taxed_equity = taxes.calculate_take_home_pay(day, equity)
                tax = (cash - taxed_cash) + (equity - taxed_equity)
                cash = taxed_cash
                equity = taxed_equity
            fields += (cash, equity, cash+equity, tax)
        data.append(fields)

    return tuple(accumulate(data, add_rows_pairwise))

def make_offer_comparison(
        *,
        argv,
        offers,
        start_date = date.today(),
        nr_years = DEFAULT_NR_YEARS,
        output = sys.stdout,
        taxes = Taxes2016,
        paydays = DEFAULT_PAYDAYS,
        already_earned_first_year = 0,
        already_earned_state = None,
        series = DEFAULT_SERIES,
        series_styles = DEFAULT_SERIES_STYLES,
        title = None,
        show_dollars = True):
    """Entry point for valleyjudge.

    ARGV is the program's argument array; you want to use sys.argv.
    OFFERS is a sequence of Offer objects.

    START_DATE is the day you expect to start working.  NR_YEARS
    is the number of years to graph.  OUTPUT is the stream to
    which to write the gnuplot file.

    TAXES is a function of one argument, a sequence of earnings days,
    that returns a Taxes instance that can answer questions about tax
    liability over the time represented.  You usually want to use the
    default, the Taxes2016 subclass of Taxes.  If TAXES is None, do
    not compute tax information; pre-tax figures will be graphed.

    PAYDAYS is a list of day-numbers indicating the days of the month
    one receives paychecks.

    ALREADY_EARNED_FIRST_YEAR and ALREADY_EARNED_STATE are an amount
    of dollars earned in the year on which you accept the offer and
    the state in which you earned that money, respectively.
    These figures are optional: they help fine-tune tax calculations.

    SERIES is a sequence of strings giving things to graph.
    The options are "total", "equity", "cash", and "tax".

    SERIES_STYLES is a mapping from series name to gnuplot line style.
    It can control the way different lines look.  All lines pertaining
    to a single offer are plotted in the same color.

    TITLE is the title of the graph.

    If SHOW_DOLLARS is true, as it is by default, the graph is labeled
    with dollar amounts.  If false, the graph does not show specific
    dollar levels.  Setting SHOW_DOLLARS to false is useful if you
    want to generate a graph showing the relative values of various
    offers without revealing exactly how much you're making.

    """

    typecheck(start_date, date)
    typecheck(nr_years, int)
    if taxes is not None and not issubclass(taxes, Taxes):
        raise TypeError(taxes)
    typecheck(paydays, seq_of(int))
    typecheck(already_earned_first_year, numbers.Real)
    typecheck(already_earned_state, (str, type(None)))
    typecheck(offers, seq_of(Offer))

    ap = ArgumentParser(description="Compare job offers")
    ap.add_argument("--debug", help="Turn on debug logging",
                    action="store_true")
    ap.add_argument("--terminal", help="gnuplot terminal string",
                    default=DEFAULT_TERMINAL)
    ap.add_argument("--output", help="gnuplot output",
                    default=None)
    ap.add_argument("--notaxes", help="Disable tax calculation",
                    action="store_true")
    args = ap.parse_args(argv[1:])

    logging_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=logging_level)

    if args.notaxes:
        taxes = None

    colors = list(reversed(AUTO_COLORS))
    offer_colors = tuple(o.color or colors.pop() for o in offers)

    data = make_earnings_table(
        offers,
        start_date,
        nr_years,
        taxes,
        already_earned_first_year,
        already_earned_state,
        paydays)

    print("$data <<EOD", file=output)
    for data_entry in data:
        print(*data_entry)
    print("EOD", file=output)
    del data
    formatting = [
        'set terminal ' + args.terminal,
    ]

    if args.output is not None:
        formatting.append('set output ' + gnuplot_quote(args.output))

    manual_top_tics = []
    epoch = datetime.utcfromtimestamp(0)
    for yearno in range(0, nr_years+3):
        anniversary = start_date.replace(year = start_date.year + yearno)
        anniversary_dt = datetime.combine(anniversary, datetime.min.time())
        anniversary_unix_time = (anniversary_dt - epoch).total_seconds()
        label = "Year %u" % (yearno+1)
        manual_top_tics.append(
            "%s %r" % (gnuplot_quote(label), anniversary_unix_time) )

    formatting.extend([
        'set decimal locale',
        'set link x',
        'set xdata time',
        'set xtics rotate by -90',
        'set x2tics (%s)' % ",".join(manual_top_tics),
        'set timefmt "%Y-%m-%d"',
        'set format x "%Y/%m"',
        'set format y "$%\'.0f"',
        'set key left',
        'set linestyle 10 lc rgb "#dddddd" lw 1',
        'set grid ytics mytics x2tics linestyle 10',
        'set mytics',
        'set y2tics',
        'set format y2 "$%\'.0f"',
    ])

    if title:
        formatting.append('set title %s' % gnuplot_quote(title))

    if not show_dollars:
        formatting.append('set format y ""')
        formatting.append('set format y2 ""')

    print("\n".join(formatting), file=output)
    print("plot \\", file=output)
    for i, offer in enumerate(offers):
        offer_column = 1 + 4*i
        for column_index, column_title in (
                (offer_column + 0, "cash"),
                (offer_column + 1, "equity"),
                (offer_column + 2, "total"),
                (offer_column + 3, "tax"),
        ):
            if column_title not in series:
                continue
            words = ["$data", "using", "1:%s" % (1 + column_index)]
            title_tags = [column_title]
            if column_title == "tax":
                if not taxes:
                    continue
            else:
                if taxes is None:
                    title_tags.append("pre-tax")
                else:
                    title_tags.append("post-tax")
            human_title = "%s %s (%s)" % (
                offer.name, offer.state, ", ".join(title_tags))
            words.extend(("title", gnuplot_quote(human_title), "noenhanced"))
            words.extend(("with", "lines"))
            words.extend(("linecolor", gnuplot_quote(offer_colors[i])))
            words.extend(series_styles.get(column_title, ()))
            print(" ".join(words) + ", \\", file=output)
    print("", file=output)
