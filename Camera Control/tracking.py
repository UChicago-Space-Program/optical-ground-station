import numpy as np

class Pixel:
    """
    Pixel class used to support the image class.
    """

    def __init__(self, pixel: tuple):
        count, x, y = pixel
        self.count = count
        self.x_pos = x
        self.y_pos = y
        self.incluster = False
        self.isjupyter = False # Attribute to track if a pixel is jupiter based on RA&Dec
        # Add RA/Dec attribute and RA/Dec tracking method

    def set_isincluster(self, bool_statement: bool):
        self.incluster = bool_statement

    def get_isincluster(self):
        return self.incluster

class Image:
    """
    Image object is characterised by a list of lists of pixels (an array of pixels in a single image). Methods
    in this class are used for identifying and tracking bright objects on the camera.
    """

    def __init__(self, pixels: list[list], threshold):
        self.pixels = []
        self.size_x = len(pixels[0])
        self.size_y = len(pixels)
        self.threshold = threshold
        # Making use of the pixel class to create pixel objects that contain informaiton about position
        for i in range(self.size_y):
            self.pixels.append([])
            for j in range(self.size_x):
                self.pixels[i].append(Pixel((pixels[i][j], j, i)))

    def filter_threshold(self):
        """
        Function that creates a list of pixels above a given threshold.
        """
        filtered_pixels = []
        for i in range(self.size_y):
            for j in range(self.size_x):
                if self.pixels[i][j].count >= self.threshold:
                    filtered_pixels.append(self.pixels[i][j])
        return filtered_pixels

    def clear_image(self):
        filtered_pixels = self.filter_threshold()
        new_image = []
        for j in range(self.size_y):
            new_image.append([])
            for i in range(self.size_x):
                new_image[j].append(0)
                for pixel in filtered_pixels:
                    if pixel.x_pos == i and pixel.y_pos == j:
                        new_image[j][i] = pixel.count
        return Image(new_image, self.threshold), new_image


    def adjacent_pixels(self, pixel: Pixel, neighbors: list):
        """
        Outputs a list of immediately adjacent pixels above the threshold to a given input pixel. This function
        is a bit "brute-forcy" but it will probably work.
        """
        x_loc = pixel.x_pos
        y_loc = pixel.y_pos
        # Only will produce a neighbor list if the provided pixel itself is ALSO above the threshold
        # Also does not allow double counting
        # Also making sure that the indexing will not be out of range
        if pixel.count >= self.threshold:
            # Pixel directly above
            if y_loc < (self.size_y - 1):
                if self.pixels[y_loc + 1][x_loc].count >= self.threshold and self.pixels[y_loc + 1][x_loc] not in neighbors:
                    neighbors.append(self.pixels[y_loc + 1][x_loc])
            # Pixel directly below
            if y_loc > 0:
                if self.pixels[y_loc - 1][x_loc].count >= self.threshold and self.pixels[y_loc -1][x_loc] not in neighbors:
                    neighbors.append(self.pixels[y_loc -1][x_loc])
            # Pixel directly to the right
            if x_loc < (self.size_x - 1):
                if self.pixels[y_loc][x_loc + 1].count >= self.threshold and self.pixels[y_loc][x_loc + 1] not in neighbors:
                    neighbors.append(self.pixels[y_loc][x_loc + 1])
            # Pixel directly to the left
            if x_loc > 0:
                if self.pixels[y_loc][x_loc - 1].count >= self.threshold and self.pixels[y_loc][x_loc - 1] not in neighbors:
                    neighbors.append(self.pixels[y_loc][x_loc - 1])
        return neighbors

    ### Need to add is_in_neighborhood condition
    ### Need to add other methods remaining