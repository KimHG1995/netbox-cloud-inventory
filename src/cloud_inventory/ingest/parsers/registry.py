from collections.abc import Sequence
from pathlib import Path

from cloud_inventory.ingest.parsers.aws_resource_explorer import (
    AwsResourceExplorerCsvParser,
)
from cloud_inventory.ingest.parsers.base import Parser, SourceMetadata
from cloud_inventory.ingest.parsers.import_bundle import ImportBundleParser
from cloud_inventory.ingest.parsers.ncp_console import (
    NcpBucketXlsxParser,
    NcpLoadBalancerXlsxParser,
    NcpPublicIpXlsxParser,
    NcpServerXlsxParser,
)


class ParserDetectionError(ValueError):
    """Base error for parser detection failures."""


class NoParserMatchError(ParserDetectionError):
    """No registered parser matched the content."""


class AmbiguousParserError(ParserDetectionError):
    """Multiple registered parsers matched with the same confidence."""


class ParserProfileMismatchError(ParserDetectionError):
    """An explicitly selected profile did not match the content."""


class ParserRegistry:
    def __init__(self, parsers: Sequence[Parser]) -> None:
        self._parsers = tuple(parsers)

    def detect(self, path: Path, metadata: SourceMetadata) -> Parser:
        if metadata.export_type == "auto":
            candidates = self._parsers
        else:
            candidates = tuple(
                parser
                for parser in self._parsers
                if parser.profile_id == metadata.export_type
            )
            if not candidates:
                raise ParserProfileMismatchError(
                    f"unknown parser profile: {metadata.export_type}"
                )

        evaluations = [
            (parser.detect(path, metadata), parser)
            for parser in candidates
        ]
        matches = [
            (result, parser)
            for result, parser in evaluations
            if result.matched
        ]
        if not matches:
            diagnostics = " | ".join(
                f"{parser.profile_id}: {result.reason}"
                for result, parser in evaluations
            )
            if metadata.export_type == "auto":
                raise NoParserMatchError(
                    f"no parser matched the uploaded content; {diagnostics}"
                )
            raise ParserProfileMismatchError(
                "content does not match parser profile "
                f"{metadata.export_type}; {diagnostics}"
            )

        best_confidence = max(result.confidence for result, _ in matches)
        best = [
            parser
            for result, parser in matches
            if result.confidence == best_confidence
        ]
        if len(best) != 1:
            profiles = ", ".join(sorted(parser.profile_id for parser in best))
            raise AmbiguousParserError(f"ambiguous parser profiles: {profiles}")
        return best[0]


def build_default_registry() -> ParserRegistry:
    return ParserRegistry(
        [
            ImportBundleParser(),
            AwsResourceExplorerCsvParser(),
            NcpServerXlsxParser(),
            NcpPublicIpXlsxParser(),
            NcpLoadBalancerXlsxParser(),
            NcpBucketXlsxParser(),
        ]
    )
