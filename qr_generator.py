import os

import qrcode


def generate_access_qr(
    code,
    base_url,
    output_folder="static/qr",
):

    os.makedirs(
        output_folder,
        exist_ok=True,
    )

    target_url = (
        f"{base_url.rstrip('/')}/q/{code}"
    )

    qr = qrcode.QRCode(
        version=None,
        error_correction=(
            qrcode.constants.ERROR_CORRECT_M
        ),
        box_size=12,
        border=4,
    )

    qr.add_data(target_url)
    qr.make(fit=True)

    image = qr.make_image()

    filename = f"{code}.png"

    filepath = os.path.join(
        output_folder,
        filename,
    )

    image.save(filepath)

    return {
        "filename": filename,
        "filepath": filepath,
        "url": target_url,
    }