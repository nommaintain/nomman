import argparse
import logging
import sys
from multiprocessing import freeze_support

import nomman.authorities as auths
import nomman.config as config
import nomman.gateways.lpsn_api as lpsn_api
import nomman.utils as utils
from nomman.credentials.providers import LazyLpsnPwdProvider
from nomman.exceptions import ExternalError, LabInventoryError, NommanError, TaxonomyError
from nomman.models import Authority
from nomman.presentation import CsvReportRenderer, HtmlReportRenderer, MdReportRenderer, ReportRenderer
from nomman.repositories import AuthorityRepo, DomainRepo, LabRepo, SystemRepo
from nomman.services.cli_handler import CliAdminHandler, CliFuzzyHandler, CliPwdProvider, parse_args, parse_fuzzy
from nomman.services.core import ClassifyOrchestrator, ClassifyReport, FuzzyPrevalCoordinator, FuzzyPrevalidation
from nomman.services.taxon_resolver import TaxonResolver
from nomman.values import ReportFormat

logger = logging.getLogger(__name__)


def main() -> int:
    args = parse_args()
    _set_logging(args.verbose, args.brief)
    sys_repo = SystemRepo()
    if args.version:
        print(f"nomman version {sys_repo.get_version()}")
        return 0
    cfg = config.init_config(args.config)
    lpsn_api.set_password_provider(pwd_provider := LazyLpsnPwdProvider(CliPwdProvider()))
    if not _handle_operational_modes(args, cfg):
        return 0
    try:
        # Phase 1: Composition Root (Dependency Wiring)
        orchestrator, authorities, lab_repo = _setup_infra(cfg, pwd_provider, sys_repo)
        if args.nomengroups is not None and cfg.domains_file and args.nomengroups != cfg.domains_file:
            logger.info(f"CLI value '{args.nomengroups}' for domain file overrides config value '{cfg.domains_file}'")
        # Phase 2: Execution Pipeline
        report = _run_pipeline(args, cfg, orchestrator, authorities, lab_repo)
        # Phase 3: Presentation
        return _render_output(args, report)
    except NommanError as e:
        return _handle_exception(e)


def _handle_exception(e: NommanError) -> int:
    prefix = "Unexpected application error"
    if isinstance(e, (TaxonomyError, LabInventoryError)):
        prefix = "Data validation error"
    elif isinstance(e, ExternalError):
        prefix = "External system/infrastructure error"
    logger.error(f"{prefix}: {e}")
    return 1


def _setup_infra(
    cfg: config.ProgConfig, pwd_provider: LazyLpsnPwdProvider, sys_repo: SystemRepo
) -> tuple[ClassifyOrchestrator, frozenset[Authority], LabRepo]:
    lab_repo, auth_repo = LabRepo(), AuthorityRepo(auths.get_nomen_providers(pwd_provider))
    authorities = auth_repo.load_authorities(cfg)
    _db_map = {"LPSN DB": "^", "Mycobank DB": "~"}  # map database symbols to gateways for taxon resolution
    if not (symbol_map := {_db_map[a.name]: a.gateway for a in authorities if a.name in _db_map and a.gateway}):
        logger.warning("No active taxonomic expansion gateways found. Domain resolution may fail.")
    orchestrator = ClassifyOrchestrator(
        auth_repo=auth_repo, domain_repo=DomainRepo(TaxonResolver(symbol_map)), lab_repo=lab_repo, sys_repo=sys_repo
    )
    return orchestrator, authorities, lab_repo


def _run_pipeline(
    args: argparse.Namespace,
    cfg: config.ProgConfig,
    orchestrator: ClassifyOrchestrator,
    authorities: frozenset[Authority],
    lab_repo: LabRepo,
) -> ClassifyReport:
    inventory = lab_repo.load_inventory(args.input, args.filelist)
    if args.normal:
        logger.info("Applying name formatting normalization...")
        inventory = orchestrator.normalize_inventory(inventory)
    (fuzzy_enabled, f_workers), replacements, suggestions = parse_fuzzy(args), None, None
    if fuzzy_enabled:
        fuzzy_coord = FuzzyPrevalCoordinator(lab_repo, FuzzyPrevalidation(), CliFuzzyHandler())
        replacements, suggestions = fuzzy_coord.resolve_replacements(inventory, authorities, f_workers)
    return orchestrator.run_classification(
        inventory=inventory,
        domains_path=args.nomengroups if args.nomengroups is not None else cfg.domains_file,
        authorities=authorities,
        replacements=replacements,
        suggestions=suggestions,
    )


def _render_output(args: argparse.Namespace, report: ClassifyReport) -> int:
    """Helper to render report as final step in program; returns exit code"""
    format_map: dict[ReportFormat, type[ReportRenderer]] = {
        ReportFormat.MARKDOWN: MdReportRenderer,
        ReportFormat.HTML: HtmlReportRenderer,
        ReportFormat.CSV: CsvReportRenderer,
    }
    try:  # pick renderer based on format argument
        _content = (format_map[ReportFormat(args.format)])().render(report)
    except (ValueError, KeyError):  # safety fallback
        logger.error(f"Unsupported report format: {args.format}")
        return 1
    if args.output:
        try:
            with open(args.output, mode="w", encoding=utils.ENC) as f:
                f.write(_content)
            logger.info(f"Report written to: {args.output}")
        except OSError as e:
            logger.error(f"Failed write report to file {args.output}: {e}")
            return 1
    else:
        print(f"\n{_content}")
    return 0


def _handle_operational_modes(args: argparse.Namespace, cfg: config.ProgConfig) -> bool:
    """Returns True for proceeding to main classification, False for exit after task."""
    if args.store_lpsn_pw:
        CliAdminHandler.store_password(cfg.lpsn_user)
        return False
    if args.clear_lpsn_pw:
        CliAdminHandler.delete_password(cfg.lpsn_user)
        return False
    if test_q := args.test_lpsn_query:
        q_tuple = ("", int(test_q)) if utils.is_int(test_q) else (test_q, -1)
        lpsn_api.taxon_query(q_tuple[0], cfg.lpsn_user, q_tuple[1], True)
        return False
    if not args.input and not args.filelist:
        print('Error: No input file(s) specified with "-i/--input" or "-l/--filelist"')
        sys.exit(1)
    return True


def _set_logging(v_flag: bool, b_flag: bool) -> None:
    if v_flag:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s [%(name)s]: %(message)s")
    elif b_flag:
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


if __name__ == "__main__":
    freeze_support()
    sys.exit(main())
