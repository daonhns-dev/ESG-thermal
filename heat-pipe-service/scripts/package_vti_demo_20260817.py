"""Package only verified offline Demo results, without source videos or secrets."""
import hashlib
import json
from pathlib import Path
import struct
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / 'output/demo-20260817-vti-20261123'
    payload = json.loads((folder / 'analysis_result.json').read_text())
    verified = json.loads((folder / 'validation.json').read_text())
    assert payload['success'] and payload['status'] == 'SUCCEEDED'
    assert verified['processed_frames'] == 3481
    assert verified['pngs_verified'] == payload['candidate_count']
    assert verified['source_manifest_sha256'] == 'ac6c9dafa140bb1cccaab78a9a32db0e8b051aef8e43fd06d95ee0e5d6a10626'
    names = ['analysis_result.json', 'analysis_candidates.csv', 'frame_matching.csv', 'validation.json', 'README.txt']
    names.extend(c['result_image']['path'] for c in payload['candidates'])
    assert len(set(names)) == len(names)
    for candidate in payload['candidates']:
        image = candidate['result_image']
        p = (folder / image['path']).resolve()
        assert p.is_relative_to(folder.resolve())
        assert p.stat().st_size == image['size_bytes']
        assert hashlib.sha256(p.read_bytes()).hexdigest() == image['sha256']
        assert struct.unpack('>II', p.read_bytes()[16:24]) == (image['width'], image['height'])
        assert 'att_offset_ms' not in candidate
    readme = f'''VTI 프레임 매칭 테스트용 Demo 이상탐지 결과
작성일: 2026-11-23
Demo: 20260817_002325

분석 결과
- 로컬 오프라인 분석 작업: {payload['job_id']} (운영 API에서 조회할 수 있는 작업 ID가 아님)
- ATT/ATG 전체 {verified['processed_frames']:,}프레임 분석, frame_stride=1, CPU.
- 출력 후보 {payload['candidate_count']}건 / 전체 적격 후보 {payload['detected_candidate_count']}건.
- 후보 최대 50건은 결과 출력 상한이며, 관로 이상 확정 건수가 아닙니다.
- 기본 ROI: 2행 3열 중 하단 가운데 칸. 위치/파라미터는 결과 JSON을 참고하세요.

파일 구성
- analysis_result.json: 기존 결과 구조의 후보/대표 프레임/시각/GPS/온도/PNG 정보.
- analysis_candidates.csv: 후보 목록.
- frame_matching.csv: VTI AVI 추출 테스트에 사용할 대표 프레임 목록.
- images/: 후보별 열화상 PNG. JSON의 result_image.path는 ZIP 내부 상대 경로입니다.
  운영 API URL이 아니며 API 응답을 직접 저장한 파일도 아닙니다.
- validation.json: 원본 파일 크기/해시, 검토 소스 식별자, 실행 환경, 검증 기록.
- SHA256SUMS.json: ZIP 내용 검증용 SHA-256 목록.

실영상 매칭 기준 (협의 제안)
- representative_frame은 원본 ATT의 0부터 시작하는 프레임 번호입니다.
- 원본 AVI를 디코딩한 순서에서 같은 번호를 추출해 대응 장면을 확인하세요.
- att_offset_ms는 추가하지 않았습니다. AVI 재생용 30fps를 실제 촬영 FPS로 간주하지 마세요.
- captured_at은 ATG 장비 기록을 KST로 해석한 초 단위 시각입니다. AVI seek 기준이 아닙니다.
- 이 Demo의 ATG 첫 레코드(00:22:20)는 다음 레코드(00:25:45)와 크게 떨어져 있어
  녹화 시작 기준이나 시간 보정값 계산에 사용하지 마세요.
- Demo의 ATT/ATG/AVI 헤더상 개수는 모두 3,481개입니다. 이 분석 자체는 정밀 동기화,
  프레임 단위 보정값이나 다른 회차의 동기화를 보증하지 않습니다.
- 변환 MP4 또는 프레임을 생략한 영상의 순번을 대신 사용하지 마세요.

실행 및 사용 범위
- 운영 배포 검토본과 같은 소스 26파일의 SHA-256을 확인해 실행했습니다.
- 로컬 Docker의 라이브러리 버전은 validation.json에 기록했습니다.
  운영 서버와 의존성까지 동일함을 확인한 실행은 아닙니다.
- ATT/ATG 해시를 분석 전후 대조했고 원본은 변경하지 않았습니다.
- 원본 ATT/ATG/AVI 및 AVI에서 추출한 실영상 이미지는 포함하지 않았습니다.
- Demo AVI의 VTI 해외 개발팀 사용 승인을 대신하는 자료가 아닙니다.
'''
    (folder / 'README.txt').write_text(readme, encoding='utf-8-sig')
    hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}
    (folder / 'SHA256SUMS.json').write_text(json.dumps(hashes, ensure_ascii=False, indent=2)+'\n')
    output = ROOT / 'output/VTI_Demo_20260817_분석결과_20261123.zip'
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in names + ['SHA256SUMS.json']:
            assert Path(name).suffix.lower() not in ('.avi', '.att', '.atg')
            archive.write(folder / name, name)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        for name, expected in hashes.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    print(json.dumps({'zip': str(output), 'zip_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'zip_bytes': output.stat().st_size, 'candidate_count': payload['candidate_count'],
                      'detected_candidate_count': payload['detected_candidate_count'],
                      'frames': verified['processed_frames']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
